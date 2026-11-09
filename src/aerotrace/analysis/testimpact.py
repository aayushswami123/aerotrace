"""Which tests to run for a change: test entry points in the index, linked to changed code by callers.

A test entry point is a function a test framework runs:

- GoogleTest: `TestBody` of a `TEST`/`TEST_F`/`TEST_P` class, and the `SetUp`/`TearDown` of a fixture.
- Unity: a function passed to `RUN_TEST(...)`.
- CMocka: a function named in `cmocka_unit_test*(...)` (the test, its setup and its teardown).
- A test program: `main` in a file named like a test (`test_*.c`, `*_test.c`, ...) or under a test folder.

Unity and CMocka take the test as a function pointer, so they are found through `address_sites`, not
through call links. A registration that is commented out or inside `#if 0` is not in the index, so the
function is not a test: it never runs.

Static links only say "this test calls the changed code". They can't prove that no test runs it (tests
written as scripts, calls from outside the index), so code no test calls is reported as "not known",
never as untested.

Recorded coverage (`.aerotrace/tests.json`, see coverage.py) adds the tests that ran the code. Only
coverage that is fresh (same file content), complete, with unchanged test inputs, can move code to
"No test reaches", and then only as `probable`.
"""

from __future__ import annotations

import os
import re
import shlex
import sqlite3
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from aerotrace.analysis import certainty, coverage, diff, graph
from aerotrace.analysis.graph import CONFIDENCE_ORDER, Hit
from aerotrace.analysis.symbols import Fn

KINDS = ("gtest", "unity", "cmocka", "main")
KIND_WORDS = {
    "gtest": "GoogleTest",
    "unity": "Unity",
    "cmocka": "CMocka",
    "main": "test program with main",
}

TEST_DIRS = {"test", "tests", "testing", "unittest", "unittests", "unit_tests", "unit-tests"}
_TEST_FILE = re.compile(
    r"^(test[_-]?\w*|\w+[_-]tests?|\w+[_-]?unittests?)\.(c|cc|cpp|cxx|c\+\+|C)$", re.IGNORECASE
)
_SOURCE_EXTS = {".c", ".cc", ".cpp", ".cxx", ".c++", ".C"}
_GTEST = re.compile(r"^\s*(TEST|TEST_F|TEST_P|TYPED_TEST|TYPED_TEST_P)\s*\(\s*(\w+)\s*,\s*(\w+)\s*\)")
_RUN_TEST = re.compile(r"\bRUN_TEST\s*\(\s*(\w+)")
_CMOCKA = re.compile(r"\bcmocka_unit_test\w*\s*\(([^()]*)\)")
_SKIP_DIRS = {".git", ".aerotrace", ".hg", ".svn", "node_modules", "build", "out", "cmake-build-debug"}


# ---------------------------------------------------------------- results


@dataclass(frozen=True)
class Entry:
    """One way a test framework runs a function."""

    fn_id: str
    kind: str  # gtest | unity | cmocka | main
    test: str  # the test's id: Suite.Name, file:function, or the file of a test program
    file: str  # the test's source file
    role: str  # "test" | "setup" (fixture SetUp/TearDown, CMocka setup/teardown) | "program"
    parametrized: bool = False  # TEST_P / TYPED_TEST: the run filter needs wildcards


@dataclass
class TestLink:
    test: str
    kind: str
    file: str
    level: str  # exact | probable
    reason: str
    reaches: list[str] = field(default_factory=list)  # changed things it reaches (names)
    parametrized: bool = False


@dataclass
class NotKnown:
    what: str  # a function name, a declaration, or a file
    where: str  # file:line or file
    why: str


@dataclass
class TestImpact:
    title: str  # what was asked: "compared with HEAD (abc1234)", "for setCommand (src/t_string.c:440)"
    changed_fns: int = 0
    changed_decls: int = 0
    files: int = 0
    tests: list[TestLink] = field(default_factory=list)
    no_test_reaches: list[NotKnown] = field(default_factory=list)  # only fresh coverage fills this
    not_known: list[NotKnown] = field(default_factory=list)
    entries: Counter = field(default_factory=Counter)  # test entry points in the index, by kind
    unindexed_tests: list[str] = field(default_factory=list)  # test-like source files not in the index
    coverage: str = "not_recorded"  # not_recorded | fresh | outdated | partial
    coverage_line: str = ""  # one plain sentence about the recorded coverage
    run_commands: list[str] = field(default_factory=list)  # ready-to-run commands for coverage tests
    depth: int = 8
    truncated: bool = False
    limits: list[str] = field(default_factory=list)
    ignored: int = 0  # files in the change that are not C or C++


# ---------------------------------------------------------------- finding test entry points


class _Lines:
    """Source lines of indexed files, read once each."""

    def __init__(self, root: Path):
        self.root = root
        self._cache: dict[str, list[str]] = {}

    def get(self, file: str, line: int) -> str:
        if file not in self._cache:
            try:
                self._cache[file] = (self.root / file).read_text(errors="replace").split("\n")
            except OSError:
                self._cache[file] = []
        lines = self._cache[file]
        return lines[line - 1] if 0 < line <= len(lines) else ""


def is_test_file(path: str) -> bool:
    p = Path(path)
    return bool(_TEST_FILE.match(p.name)) or any(part.lower() in TEST_DIRS for part in p.parts[:-1])


def _bases(conn: sqlite3.Connection) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for r in conn.execute("SELECT class, base FROM class_bases ORDER BY class, base"):
        out.setdefault(r[0], []).append(r[1])
    return out


def _ancestors(cls: str, bases: dict[str, list[str]]) -> list[str]:
    seen: list[str] = []
    todo = list(bases.get(cls, []))
    while todo:
        b = todo.pop(0)
        if b not in seen:
            seen.append(b)
            todo += bases.get(b, [])
    return seen


def find_entries(conn: sqlite3.Connection, root: Path) -> dict[str, list[Entry]]:
    """Every test entry point in the index: {function id: [entries]} (one setup can serve many tests)."""
    lines = _Lines(root)
    out: dict[str, list[Entry]] = {}

    def add(e: Entry) -> None:
        if e not in out.setdefault(e.fn_id, []):
            out[e.fn_id].append(e)

    # GoogleTest: TestBody of a class the TEST macro made, on the line where the macro is written.
    bases = _bases(conn)
    setups: dict[str, list[str]] = {}  # fixture class -> SetUp/TearDown function ids
    for r in conn.execute(
        "SELECT id, container FROM functions WHERE name IN ('SetUp', 'TearDown') AND container IS NOT NULL"
    ):
        setups.setdefault(r["container"], []).append(r["id"])
    for r in conn.execute(
        """SELECT id, container, file, line_start FROM functions
           WHERE name = 'TestBody' AND container LIKE '%\\_Test' ESCAPE '\\' ORDER BY file, line_start, id"""
    ):
        m = _GTEST.match(lines.get(r["file"], r["line_start"]))
        if not m:
            continue
        macro, suite, name = m.groups()
        if r["container"].split("::")[-1] != f"{suite}_{name}_Test":
            continue
        test = f"{suite}.{name}"
        param = macro in ("TEST_P", "TYPED_TEST", "TYPED_TEST_P")
        add(Entry(r["id"], "gtest", test, r["file"], "test", param))
        for base in _ancestors(r["container"], bases):
            if base == "testing::Test":
                continue
            for sid in setups.get(base, []):
                add(Entry(sid, "gtest", test, r["file"], "setup", param))

    # Unity and CMocka: the test function is passed as a pointer where it is registered.
    for r in conn.execute(
        """SELECT a.function_id AS fid, f.name AS name, a.file AS file, a.line AS line
           FROM address_sites a JOIN functions f ON f.id = a.function_id
           ORDER BY a.file, a.line, a.function_id"""
    ):
        text = lines.get(r["file"], r["line"])
        for m in _RUN_TEST.finditer(text):
            if m.group(1) == r["name"]:
                add(Entry(r["fid"], "unity", f"{r['file']}:{r['name']}", r["file"], "test"))
        for m in _CMOCKA.finditer(text):
            args = [a.strip() for a in m.group(1).split(",")]
            if r["name"] in args and args[0]:
                role = "test" if args[0] == r["name"] else "setup"
                add(Entry(r["fid"], "cmocka", f"{r['file']}:{args[0]}", r["file"], role))

    # Test programs: main in a test file.
    for r in conn.execute("SELECT id, file FROM functions WHERE name = 'main' ORDER BY file, id"):
        if is_test_file(r["file"]):
            add(Entry(r["id"], "main", r["file"], r["file"], "program"))
    return out


def unindexed_test_files(conn: sqlite3.Connection, root: Path, limit: int = 2000) -> list[str]:
    """Test-like C/C++ source files on disk that the index does not have (their tests can't be seen)."""
    known = {r[0] for r in conn.execute("SELECT path FROM files")}
    found: list[str] = []
    seen = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            if Path(name).suffix not in _SOURCE_EXTS:
                continue
            seen += 1
            if seen > limit * 50:
                return sorted(found)
            rel = Path(dirpath, name).relative_to(root).as_posix()
            if rel not in known and is_test_file(rel):
                found.append(rel)
    return sorted(found)


# ---------------------------------------------------------------- linking


@dataclass
class _Item:
    """One changed thing the walk starts from."""

    name: str
    where: str
    starts: list[Fn]
    seeds: dict[str, tuple[str, str, list[int]]]
    labels: dict[str, str]
    # functions to look up in recorded coverage: (file, qualified name, "self" | "user")
    cov_keys: list[tuple[str, str, str]] = field(default_factory=list)
    new: bool = False  # a function the change adds: no coverage can exist yet


class _CovContext:
    """Recorded coverage, and how far it can be trusted for this change."""

    def __init__(self, root: Path, data: coverage.Coverage, base_text: Callable[[str], bytes | None] | None):
        self.root = root
        self.data = data
        self.base_text = base_text
        self.failed = coverage.failed(data)
        self.inputs_changed = coverage.changed_inputs(root, data)
        self.states: dict[str, str] = {}

    def state(self, file: str) -> str:
        if file not in self.states:
            base = self.base_text(file) if self.base_text else None
            self.states[file] = coverage.file_state(self.root, self.data, file, base)
        return self.states[file]

    @property
    def trusted(self) -> bool:
        """Can an empty result mean "none of the recorded runs executed it"?"""
        return self.data.complete and not self.inputs_changed

    def where(self) -> str:
        at = f" at {self.data.commit[:12]}" if self.data.commit else ""
        return f"recorded{at}"


def _cov_links(ctx: _CovContext, it: _Item) -> tuple[dict[str, tuple[str, str]], str | None, bool]:
    """({test: (level, reason)}, why coverage can't answer (or None), whether every changed function was
    seen built with coverage, in a fresh file, and run by no recorded test)."""
    ran: dict[str, tuple[str, str]] = {}
    problem: str | None = None
    zero = bool(it.cov_keys) and any(r == "self" for _, _, r in it.cov_keys)
    for file, q, role in it.cov_keys:
        st = ctx.state(file)
        fc = ctx.data.files.get(file)
        if role == "self" and it.new:
            problem, zero = "new; no coverage was recorded for it yet", False
            continue
        if st == "missing" or fc is None:
            if role == "self":
                problem = (
                    problem
                    or f"no coverage data for {file} (not built with coverage, or in no recorded test)"
                )
                zero = False
            continue
        key = coverage.find_fn(fc, q, 0)
        if key is None:
            if role == "self":
                problem = (
                    problem or "not in the coverage data (not built with coverage, or new since recording)"
                )
                zero = False
            continue
        tests = fc.functions[key]
        if role == "self" and (st != "fresh" or tests):
            zero = False
        if role == "self" and st != "fresh":
            problem = problem or f"{file} changed since coverage was {ctx.where()}"
        for t in sorted(tests):
            level = certainty.EXACT if (st == "fresh" and role == "self") else certainty.PROBABLE
            why = (
                "ran it when coverage was recorded"
                if role == "self"
                else f"ran {q}, which uses it, when recorded"
            )
            if st != "fresh":
                why += f"; {file} changed since"
            if t in ctx.failed:
                why += " (failed when recorded)"
            cur = ran.get(t)
            if cur is None or (cur[0] != certainty.EXACT and level == certainty.EXACT):
                ran[t] = (level, why)
    return ran, problem, zero


def _path(hit_by_name: dict[str, Hit], h: Hit) -> list[str]:
    """From the hit down to the changed thing, following `via` (at most 6 names)."""
    names = [h.fn.qualified_name]
    via = h.via
    while via and via not in names and len(names) < 6:
        names.append(via)
        nxt = hit_by_name.get(via)
        if nxt is None:
            break
        via = nxt.via
    return names


def _reason(conf: str, path: list[str]) -> str:
    chain = " -> ".join(path)
    if conf == "exact":
        return f"calls it: {chain}"
    if conf == "uses_changed":
        return f"uses the changed declaration: {chain}"
    if conf == "calls_removed":
        return f"still calls the removed function: {chain}"
    if conf == "data":
        return f"reads data it writes: {chain}"
    return f"{certainty.of_call(conf)[1]}: {chain}"


def _link(
    conn: sqlite3.Connection,
    res: TestImpact,
    items: list[_Item],
    entries: dict[str, list[Entry]],
    depth: int,
    ctx: _CovContext | None = None,
) -> None:
    rank = CONFIDENCE_ORDER.index
    links: dict[tuple[str, str], tuple[TestLink, str]] = {}  # (kind, test) -> (link, confidence)
    for it in items:
        hits, truncated = graph.walk_many(conn, it.starts, "callers", depth, seeds=it.seeds, labels=it.labels)
        res.truncated |= truncated
        by_name: dict[str, Hit] = {}
        for h in hits:
            by_name.setdefault(h.fn.qualified_name, h)
        # (function, confidence, path); a path of None means the entry point itself changed
        reached: list[tuple[Fn, str, list[str] | None]] = [(f, "exact", None) for f in it.starts]
        reached += [(h.fn, h.confidence, _path(by_name, h)) for h in hits]
        found = False
        for fn, conf, path in reached:
            for e in entries.get(fn.id, []):
                found = True
                key = (e.kind, e.test)
                if path is None:
                    why = "its setup changed" if e.role == "setup" else "this test itself changed"
                else:
                    why = ("its setup " if e.role == "setup" else "") + _reason(conf, path)
                level = "exact" if conf == "exact" else certainty.PROBABLE
                cur = links.get(key)
                if cur is None:
                    links[key] = (
                        TestLink(e.test, e.kind, e.file, level, why, [it.name], e.parametrized),
                        conf,
                    )
                else:
                    link, best = cur
                    if it.name not in link.reaches:
                        link.reaches.append(it.name)
                    if rank(conf) < rank(best):
                        link.level, link.reason = level, why
                        links[key] = (link, conf)
        cov_problem, zero = None, False
        if ctx is not None:
            ran, cov_problem, zero = _cov_links(ctx, it)
            for t, (level, why) in ran.items():
                found = True
                key = ("coverage", t)
                conf = "exact" if level == certainty.EXACT else "via_pointer"  # for ranking only
                cur = links.get(key)
                if cur is None:
                    links[key] = (TestLink(t, "coverage", "", level, why, [it.name]), conf)
                else:
                    link, best = cur
                    if it.name not in link.reaches:
                        link.reaches.append(it.name)
                    if rank(conf) < rank(best):
                        link.level, link.reason = level, why
                        links[key] = (link, conf)
        if found:
            continue
        if ctx is not None and zero and ctx.trusted and not truncated and cov_problem is None:
            # Only fresh coverage can say this, and never with certainty (see coverage.py).
            n = len(ctx.data.tests)
            res.no_test_reaches.append(
                NotKnown(
                    it.name,
                    it.where,
                    f"probable: built with coverage, none of the {n} recorded runs executed it",
                )
            )
            continue
        why = "no C/C++ test calls it"
        if truncated:
            why += f" within {depth} call steps; more callers exist further up"
        if ctx is None:
            pass
        elif cov_problem:
            why += f"; {cov_problem}"
        elif zero and not ctx.data.complete:
            why += "; no recorded run executed it, but the recording is partial"
        elif zero and ctx.inputs_changed:
            why += "; no recorded run executed it, but test files changed since recording"
        elif zero:
            why += "; no recorded run executed it"
        else:
            why += "; coverage does not cover it"
        res.not_known.append(NotKnown(it.name, it.where, why))
    order = {certainty.EXACT: 0, certainty.PROBABLE: 1, certainty.UNKNOWN: 2}
    res.tests = sorted(
        (link for link, _ in links.values()),
        key=lambda t: (order[t.level], -len(t.reaches), t.test, t.kind),
    )
    for t in res.tests:
        t.reaches.sort()
    res.no_test_reaches.sort(key=lambda n: (n.where.split(":")[0], _line(n.where), n.what))
    if ctx is not None:
        ids = [t.test for t in res.tests if t.kind == "coverage"]
        if ids:
            from aerotrace.toolchain.testrun import ready_command

            d = ctx.data
            res.run_commands = ready_command(d.run, d.run_many, d.each, ids)


def _fn_item(conn: sqlite3.Connection, fn: Fn, new: bool = False) -> _Item:
    _, readers = graph.data_coupling(conn, fn.id)
    seeds = {rid: ("data", fn.id, lines) for rid, (_, lines) in sorted(readers.items()) if rid != fn.id}
    return _Item(
        fn.qualified_name,
        f"{fn.file}:{fn.line_start}",
        [fn],
        seeds,
        {},
        [(fn.file, fn.qualified_name, "self")],
        new,
    )


def _coverage(
    root: Path, res: TestImpact, files: list[str], base_text: Callable[[str], bytes | None] | None
) -> _CovContext | None:
    data, note = coverage.load(root)
    if data is None:
        res.coverage = "not_recorded"
        res.coverage_line = note or (
            "Coverage: not recorded. Run `aerotrace tests record` to learn which tests run this code."
        )
        return None
    ctx = _CovContext(root, data, base_text)
    n = len(data.tests)
    ran = sum(1 for t in data.tests if t.exit != "not run")
    head = f"{n} tests {ctx.where()} (.aerotrace/{coverage.path(root, not data.complete).name})"
    if not data.complete:
        head = f"partial, {ran} of {n} tests {ctx.where()}"
    files = sorted(set(files))
    outdated = [f for f in files if ctx.state(f) == "outdated"]
    missing = [f for f in files if ctx.state(f) == "missing"]
    bits = []
    if outdated:
        bits.append(f"outdated for {len(outdated)} changed file(s) ({', '.join(outdated[:3])} changed since)")
    if missing:
        bits.append(f"no data for {len(missing)} changed file(s) ({', '.join(missing[:3])})")
    if ctx.inputs_changed:
        bits.append(f"test files changed since: {', '.join(ctx.inputs_changed[:3])}")
    if not bits and files:
        bits.append("fresh for every changed file")
    res.coverage_line = f"Coverage: {head}" + (f"; {'; '.join(bits)}." if bits else ".")
    res.coverage = (
        "partial" if not data.complete else "outdated" if (outdated or ctx.inputs_changed) else "fresh"
    )
    return ctx


def _base_limits(res: TestImpact, ctx: _CovContext | None) -> list[str]:
    out = [
        "Static links: a listed C/C++ test calls the changed code on some path, but may not run the changed "
        "lines. Tests that reach it through code AeroTrace can't see (scripts, other languages, calls from "
        "outside the index) are found only through recorded coverage."
    ]
    if res.coverage == "not_recorded":
        out.append("Coverage: not recorded, so tests written as scripts (Tcl, Python, shell) are not seen.")
    if ctx is not None:
        out.append("Coverage is per recorded test (as the config lists them), not per test case.")
        out.append(
            "Coverage misses code that ran in a process killed by a signal, ended by _exit() or abort(), that "
            "crashed or timed out, and paths that vary between runs. So 'No test reaches' is never certain."
        )
        if ctx.failed:
            out.append(
                f"{len(ctx.failed)} recorded test(s) did not pass or did not finish ({_names(sorted(ctx.failed))}); "
                "their coverage may be partial."
            )
        if not ctx.data.config.get("inputs"):
            out.append(
                "Test files are not hashed (no `inputs` in the config), so edits to tests since recording are "
                "not noticed."
            )
    if res.unindexed_tests:
        shown = ", ".join(res.unindexed_tests[:3]) + (
            f" and {len(res.unindexed_tests) - 3} more" if len(res.unindexed_tests) > 3 else ""
        )
        n = len(res.unindexed_tests)
        out.append(
            f"{n} test source {'file is' if n == 1 else 'files are'} not in the index, so "
            f"{'its' if n == 1 else 'their'} tests are not seen: {shown}."
        )
    if res.truncated:
        out.append(f"Stopped at depth {res.depth}; tests further up are not listed.")
    return out


def for_diff(
    conn: sqlite3.Connection,
    root: Path,
    d: diff.DiffImpact,
    depth: int = 8,
    base_text: Callable[[str], bytes | None] | None = None,
) -> TestImpact:
    """Tests for a change already mapped by `diff.analyse`. `base_text(path)` gives a file's content on
    the base side of the change (git mode), so coverage recorded before the edit still counts as fresh."""
    title = f"compared with {d.base}" if d.mode == "git" else "for a patch that is not applied yet"
    res = TestImpact(title, depth=depth, files=d.files, ignored=d.ignored)
    res.changed_fns = len(d.changed)
    res.changed_decls = len(d.changed_vars)
    entries = find_entries(conn, root)
    res.entries = _count(entries)
    res.unindexed_tests = unindexed_test_files(conn, root)
    files = [c.fn.file for c in d.changed] + [r.file for r in d.removed]
    ctx = _coverage(root, res, files, base_text)

    items = [_fn_item(conn, c.fn, new=c.state == "new") for c in d.changed]
    for v in d.changed_vars:
        users = conn.execute(
            """SELECT e.src, e.line, f.file, f.qualified_name FROM edges e JOIN functions f ON f.id = e.src
               WHERE e.dst = ? AND e.kind IN ('reads', 'writes') ORDER BY e.src, e.line""",
            (v.id,),
        ).fetchall()
        seeds: dict[str, tuple[str, str, list[int]]] = {}
        for u in users:
            seeds.setdefault(u[0], ("uses_changed", v.id, []))[2].append(u[1])
        keys = sorted({(u[2], u[3], "user") for u in users})
        items.append(_Item(f"{v.name} ({v.kind})", f"{v.file}:{v.line}", [], seeds, {v.id: v.name}, keys))
    for r in d.removed:
        key = f"removed:{r.file}:{r.line_start}:{r.name}"
        seeds = {c.id: ("calls_removed", key, []) for c in r.still_called_by}
        keys = [
            (r.file, r.name, "self"),
            *sorted({(c.file, c.qualified_name, "user") for c in r.still_called_by}),
        ]
        items.append(
            _Item(
                f"{r.name} (removed)",
                f"{r.file}:{r.line_start}",
                [],
                seeds,
                {key: f"{r.name} (removed)"},
                keys,
            )
        )
    _link(conn, res, items, entries, depth, ctx)

    for o in d.outside:
        bits = []
        if o.lines:
            bits.append(f"lines {diff.ranges(o.lines)}")
        if o.other_side:
            side = "old" if d.mode == "git" else "new"
            bits.append(f"lines {diff.ranges(o.other_side)} ({side} numbering)")
        res.not_known.append(
            NotKnown(
                o.file,
                o.file,
                f"{' and '.join(bits)} changed outside any function (macros, types, initializers); "
                "who uses them is not followed",
            )
        )
    for f in d.not_indexed:
        res.not_known.append(NotKnown(f, f, "not in the index"))
    for f in d.new_files:
        res.not_known.append(NotKnown(f, f, "new in the patch; not indexed yet"))
    for f in d.mismatched:
        res.not_known.append(NotKnown(f, f, "the patch does not match the file on disk, so it was skipped"))
    for f in d.unreadable:
        res.not_known.append(NotKnown(f, f, "its old version could not be read; removed functions not known"))
    for f in d.deleted:
        if f not in d.unreadable:
            res.not_known.append(NotKnown(f, f, "deleted; tests that used it are not followed"))
    res.not_known.sort(key=lambda n: (n.where.split(":")[0], _line(n.where), n.what))
    res.limits = _base_limits(res, ctx) + [n for n in d.blind_spots if not n.startswith("Stopped at depth")]
    return res


def for_function(conn: sqlite3.Connection, root: Path, fn: Fn, depth: int = 8) -> TestImpact:
    """Tests that reach one function, as if it changed."""
    res = TestImpact(f"for {fn.qualified_name} ({fn.where})", depth=depth, changed_fns=1, files=1)
    entries = find_entries(conn, root)
    res.entries = _count(entries)
    res.unindexed_tests = unindexed_test_files(conn, root)
    ctx = _coverage(root, res, [fn.file], None)
    _link(conn, res, [_fn_item(conn, fn)], entries, depth, ctx)
    res.limits = _base_limits(res, ctx) + graph.index_limits(conn)
    return res


def _names(items: list[str], n: int = 3) -> str:
    return ", ".join(items[:n]) + (f" and {len(items) - n} more" if len(items) > n else "")


def git_base_text(root: Path, ref: str) -> Callable[[str], bytes | None]:
    """A file's content at the base of a git-mode change (where HEAD left `ref`), read-only."""
    from aerotrace.toolchain import git

    try:
        sha = git.resolve_base(root, ref).sha
    except git.GitError:
        return lambda _path: None

    def text(path: str) -> bytes | None:
        t = git.show(root, sha, path)
        return t.encode("utf-8", "surrogateescape") if t is not None else None

    return text


def _count(entries: dict[str, list[Entry]]) -> Counter:
    tests = {(e.kind, e.test) for es in entries.values() for e in es}
    return Counter(k for k, _ in tests)


def _line(where: str) -> int:
    tail = where.rsplit(":", 1)
    return int(tail[1]) if len(tail) == 2 and tail[1].isdigit() else 0


# ---------------------------------------------------------------- how to run them


def commands(res: TestImpact, limit: int = 20) -> list[str]:
    """How to run the listed tests, as far as the index can tell. No binary names are guessed."""
    out: list[str] = []
    gtests: dict[str, list[TestLink]] = {}
    for t in res.tests:
        if t.kind == "gtest":
            gtests.setdefault(t.file, []).append(t)
    for file, ts in sorted(gtests.items()):
        pats = sorted({f"*{t.test}*" if t.parametrized else t.test for t in ts})
        out.append(
            f"GoogleTest, in the binary built from {file}: --gtest_filter={shlex.quote(':'.join(pats))}"
        )
    for kind in ("unity", "cmocka", "main"):
        files = sorted({t.file for t in res.tests if t.kind == kind})
        for f in files:
            what = {"unity": "the Unity runner", "cmocka": "the CMocka program", "main": "the test program"}
            out.append(f"{what[kind]} built from {f}")
    return out[:limit]
