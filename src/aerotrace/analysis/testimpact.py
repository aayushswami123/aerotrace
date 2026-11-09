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
"""

from __future__ import annotations

import os
import re
import shlex
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from aerotrace.analysis import certainty, diff, graph
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
    conn: sqlite3.Connection, res: TestImpact, items: list[_Item], entries: dict[str, list[Entry]], depth: int
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
        if not found:
            why = "no C/C++ test calls it"
            if truncated:
                why += f" within {depth} call steps; more callers exist further up"
            res.not_known.append(NotKnown(it.name, it.where, why))
    order = {certainty.EXACT: 0, certainty.PROBABLE: 1, certainty.UNKNOWN: 2}
    res.tests = sorted(
        (link for link, _ in links.values()),
        key=lambda t: (order[t.level], -len(t.reaches), t.test, t.kind),
    )
    for t in res.tests:
        t.reaches.sort()


def _fn_item(conn: sqlite3.Connection, fn: Fn) -> _Item:
    _, readers = graph.data_coupling(conn, fn.id)
    seeds = {rid: ("data", fn.id, lines) for rid, (_, lines) in sorted(readers.items()) if rid != fn.id}
    return _Item(fn.qualified_name, f"{fn.file}:{fn.line_start}", [fn], seeds, {})


def _base_limits(res: TestImpact) -> list[str]:
    out = [
        "Static links only: a listed test calls the changed code on some path, but may not run the changed "
        "lines. Tests that reach it through code AeroTrace can't see (scripts, other languages, calls from "
        "outside the index) are not listed."
    ]
    if res.coverage == "not_recorded":
        out.append("Coverage: not recorded, so tests written as scripts (Tcl, Python, shell) are not seen.")
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


def for_diff(conn: sqlite3.Connection, root: Path, d: diff.DiffImpact, depth: int = 8) -> TestImpact:
    """Tests for a change already mapped by `diff.analyse`."""
    title = f"compared with {d.base}" if d.mode == "git" else "for a patch that is not applied yet"
    res = TestImpact(title, depth=depth, files=d.files, ignored=d.ignored)
    res.changed_fns = len(d.changed)
    res.changed_decls = len(d.changed_vars)
    entries = find_entries(conn, root)
    res.entries = _count(entries)
    res.unindexed_tests = unindexed_test_files(conn, root)

    items = [_fn_item(conn, c.fn) for c in d.changed]
    for v in d.changed_vars:
        users = conn.execute(
            "SELECT src, line FROM edges WHERE dst = ? AND kind IN ('reads', 'writes') ORDER BY src, line",
            (v.id,),
        ).fetchall()
        seeds: dict[str, tuple[str, str, list[int]]] = {}
        for u in users:
            seeds.setdefault(u["src"], ("uses_changed", v.id, []))[2].append(u["line"])
        items.append(_Item(f"{v.name} ({v.kind})", f"{v.file}:{v.line}", [], seeds, {v.id: v.name}))
    for r in d.removed:
        key = f"removed:{r.file}:{r.line_start}:{r.name}"
        seeds = {c.id: ("calls_removed", key, []) for c in r.still_called_by}
        items.append(
            _Item(f"{r.name} (removed)", f"{r.file}:{r.line_start}", [], seeds, {key: f"{r.name} (removed)"})
        )
    _link(conn, res, items, entries, depth)

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
    res.limits = _base_limits(res) + [n for n in d.blind_spots if not n.startswith("Stopped at depth")]
    return res


def for_function(conn: sqlite3.Connection, root: Path, fn: Fn, depth: int = 8) -> TestImpact:
    """Tests that reach one function, as if it changed."""
    res = TestImpact(f"for {fn.qualified_name} ({fn.where})", depth=depth, changed_fns=1, files=1)
    entries = find_entries(conn, root)
    res.entries = _count(entries)
    res.unindexed_tests = unindexed_test_files(conn, root)
    _link(conn, res, [_fn_item(conn, fn)], entries, depth)
    res.limits = _base_limits(res) + graph.index_limits(conn)
    return res


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
