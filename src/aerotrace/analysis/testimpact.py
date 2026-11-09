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
# Calls that run a Unity test: RUN_TEST expands to UnityDefaultTestRun; Unity's generated runners
# (generate_test_runner.rb, used by Ceedling) call their own static run_test().
_UNITY_RUNNERS = ("UnityDefaultTestRun", "UnityDefaultTestRunWithParams", "run_test")
_SKIP_DIRS = {".git", ".aerotrace", ".hg", ".svn", "node_modules"}
_GUESSED = "; registered in a way AeroTrace does not fully recognise"
_AROUND = 3  # a registration macro split over lines: clang reports one of them, the name may be on another


# ---------------------------------------------------------------- results


@dataclass(frozen=True)
class Entry:
    """One way a test framework runs a function."""

    fn_id: str
    kind: str  # gtest | unity | cmocka | main
    test: str  # the test's id: Suite.Name, file:function, or the file of a test program
    file: str  # the test's source file
    role: str  # "test" | "setup" (fixture SetUp/TearDown, CMocka setup/teardown) | "program"
    gtest_filter: str = ""  # GoogleTest: the --gtest_filter pattern(s) that select this test
    guessed: bool = False  # the registration was not recognised for sure (see find_entries)


@dataclass
class TestLink:
    test: str
    kind: str
    file: str
    level: str  # exact | probable
    reason: str
    reaches: list[str] = field(default_factory=list)  # changed things it reaches (names)
    gtest_filter: str = ""


@dataclass
class NotKnown:
    what: str  # a function name, a declaration, or a file
    where: str  # file:line or file
    why: str
    followed: bool = True  # False when AeroTrace did not follow who uses it (macros, unindexed files)


@dataclass
class Entries:
    """Test entry points, and what looked like a registration but was not recognised."""

    by_fn: dict[str, list[Entry]] = field(default_factory=dict)
    unrecognised: list[str] = field(default_factory=list)  # "name (file:line)" passed as pointers in tests
    unparsed_tests: list[str] = field(default_factory=list)  # test files with parse errors
    missing_headers: list[str] = field(default_factory=list)  # headers those files could not find

    def get(self, fid: str) -> list[Entry]:
        return self.by_fn.get(fid, [])


def gtest_filter(macro: str, suite: str, name: str) -> str:
    """The --gtest_filter pattern for one test, from GoogleTest's naming rules:
    TEST/TEST_F: `Suite.Name`. TEST_P: `Prefix/Suite.Name/0` (or `Suite.Name/0` with an empty prefix).
    TYPED_TEST: `Suite/0.Name`. TYPED_TEST_P: `Prefix/Suite/0.Name`. The patterns may also match a few
    other tests (more is safe); they never miss these."""
    if macro in ("TEST", "TEST_F"):
        return f"{suite}.{name}"
    if macro == "TEST_P":
        return f"*{suite}.{name}/*"
    if macro in ("TYPED_TEST", "TYPED_TEST_P"):
        return f"*{suite}/*.{name}"
    # Unknown macro (a project's wrapper): match every naming style.
    return f"*{suite}*.{name}*"


@dataclass
class TestImpact:
    title: str  # what was asked: "compared with HEAD (abc1234)", "for setCommand (src/t_string.c:440)"
    changed_fns: int = 0
    removed_fns: int = 0
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

    def span(self, file: str, first: int, last: int) -> str:
        """Lines first..last joined with spaces (for a macro call split over several lines)."""
        return " ".join(self.get(file, n) for n in range(max(1, first), last + 1))


def is_test_file(path: str) -> bool:
    p = Path(path)
    return bool(_TEST_FILE.match(p.name)) or any(part.lower() in TEST_DIRS for part in p.parts[:-1])


def _bases(conn: sqlite3.Connection) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for r in conn.execute("SELECT class, base FROM class_bases ORDER BY class, base"):
        out.setdefault(r[0], []).append(r[1])
    return out


def _plain(cls: str) -> str:
    """`ns::Fixture<T>` -> `ns::Fixture` (template arguments don't matter for finding the class)."""
    return re.sub(r"<.*>$", "", cls)


def _ancestors(cls: str, bases: dict[str, list[str]]) -> list[str]:
    plain = {_plain(k): v for k, v in bases.items()}
    seen: list[str] = []
    todo = list(plain.get(_plain(cls), []))
    while todo:
        b = _plain(todo.pop(0))
        if b not in seen:
            seen.append(b)
            todo += plain.get(b, [])
    return seen


def _gtest_names(cls: str) -> list[tuple[str, str]]:
    """Possible (suite, name) pairs behind a class named `<Suite>_<Name>_Test`. GoogleTest asks for names
    without underscores; when they have some anyway, every split is returned."""
    core = cls.split("::")[-1][: -len("_Test")]
    parts = core.split("_")
    return [("_".join(parts[:i]), "_".join(parts[i:])) for i in range(1, len(parts))]


def _gtest(conn: sqlite3.Connection, lines: _Lines, add) -> None:
    """GoogleTest: TestBody of a class the TEST macros made, and the fixture's SetUp/TearDown."""
    bases = _bases(conn)
    setups: dict[str, list[str]] = {}  # fixture class -> SetUp/TearDown function ids
    for r in conn.execute(
        "SELECT id, container FROM functions WHERE name IN ('SetUp', 'TearDown') AND container IS NOT NULL"
    ):
        setups.setdefault(_plain(r["container"]), []).append(r["id"])
    overrides = {
        r[0]
        for r in conn.execute(
            """SELECT e.src FROM edges e JOIN functions f ON f.id = e.dst
               WHERE e.kind = 'overrides' AND f.qualified_name = 'testing::Test::TestBody'"""
        )
    }
    for r in conn.execute(
        """SELECT id, container, file, line_start FROM functions
           WHERE name = 'TestBody' AND container LIKE '%\\_Test' ESCAPE '\\' ORDER BY file, line_start, id"""
    ):
        cls = _plain(r["container"])
        ancestors = _ancestors(cls, bases)
        is_gtest = "testing::Test" in ancestors or r["id"] in overrides
        # The macro may be split over lines (clang-format does that for long names).
        m = _GTEST.match(lines.span(r["file"], r["line_start"], r["line_start"] + 2))
        if m and cls.split("::")[-1] == f"{m.group(2)}_{m.group(3)}_Test":
            macro, suite, name = m.groups()
            test, filt, guessed = f"{suite}.{name}", gtest_filter(macro, suite, name), False
        elif is_gtest:
            # A project macro around TEST (or a layout the line check does not read): the class is a
            # GoogleTest test, but which macro made it is not known, so the filter matches every style.
            splits = _gtest_names(cls)
            if not splits:
                continue
            test = f"{splits[0][0]}.{splits[0][1]}"
            filt = ":".join(gtest_filter("", s, n) for s, n in splits)
            guessed = len(splits) > 1
        else:
            continue
        add(Entry(r["id"], "gtest", test, r["file"], "test", filt, guessed))
        for base in ancestors:
            if base == "testing::Test":
                continue
            for sid in setups.get(base, []):
                add(Entry(sid, "gtest", test, r["file"], "setup", filt, guessed))


def find_entries(conn: sqlite3.Connection, root: Path) -> Entries:
    """Every test entry point in the index (one setup can serve many tests), plus what looked like a test
    registration but could not be recognised, so the answer can say so."""
    lines = _Lines(root)
    res = Entries()

    def add(e: Entry) -> None:
        if e not in res.by_fn.setdefault(e.fn_id, []):
            res.by_fn[e.fn_id].append(e)

    _gtest(conn, lines, add)

    # Unity and CMocka take the test as a pointer where it is registered. Read the macro's expansion
    # (`how`: "passed to UnityDefaultTestRun() as argument 1"), so wrapper macros and split lines work;
    # fall back on the macro text, a few lines up, for CMocka's struct initializers.
    sites = conn.execute(
        """SELECT a.function_id AS fid, f.name AS name, f.file AS fn_file, a.file AS file, a.line AS line,
                  a.how AS how, a.in_function AS in_fn
           FROM address_sites a JOIN functions f ON f.id = a.function_id
           ORDER BY a.file, a.line, a.function_id"""
    ).fetchall()
    cmocka_by_fn: dict[str | None, set[str]] = {}  # registering function -> CMocka test ids
    group_setups: list[sqlite3.Row] = []
    handled: set[tuple[str, str, int]] = set()
    for r in sites:
        key = (r["fid"], r["file"], r["line"])
        how = r["how"] or ""
        if any(how.startswith(f"passed to {fn}() as argument 1") for fn in _UNITY_RUNNERS):
            add(Entry(r["fid"], "unity", f"{r['file']}:{r['name']}", r["file"], "test"))
            handled.add(key)
            continue
        if how.startswith("passed to _cmocka_run_group_tests() as argument "):
            group_setups.append(r)
            handled.add(key)
            continue
        text = lines.span(r["file"], r["line"] - _AROUND, r["line"] + _AROUND)
        if any(m.group(1) == r["name"] for m in _RUN_TEST.finditer(text)):
            add(Entry(r["fid"], "unity", f"{r['file']}:{r['name']}", r["file"], "test"))
            handled.add(key)
            continue
        for m in _CMOCKA.finditer(text):
            args = [a.strip() for a in m.group(1).split(",")]
            if r["name"] in args and args[0]:
                role = "test" if args[0] == r["name"] else "setup"
                tid = f"{r['file']}:{args[0]}"
                add(Entry(r["fid"], "cmocka", tid, r["file"], role))
                cmocka_by_fn.setdefault(r["in_fn"], set()).add(tid)
                handled.add(key)
    for r in group_setups:  # cmocka_run_group_tests(tests, group_setup, group_teardown)
        for tid in sorted(cmocka_by_fn.get(r["in_fn"], set())):
            add(Entry(r["fid"], "cmocka", tid, r["file"], "setup"))

    # Left over: a function named test* passed as a pointer in a test file is very likely registered some
    # way not recognised here (a generated runner's table, a project macro): count it as a test, marked
    # guessed. Anything else passed as a pointer in a test file is listed in the limits.
    for r in sites:
        if (r["fid"], r["file"], r["line"]) in handled or r["fid"] in res.by_fn:
            continue
        if not (is_test_file(r["file"]) or is_test_file(r["fn_file"])):
            continue
        if r["name"].lower().startswith("test"):
            add(Entry(r["fid"], "unity", f"{r['file']}:{r['name']}", r["file"], "test", guessed=True))
        else:
            item = f"{r['name']} ({r['file']}:{r['line']})"
            if item not in res.unrecognised:
                res.unrecognised.append(item)

    # Test programs: main in a test file.
    for r in conn.execute("SELECT id, file FROM functions WHERE name = 'main' ORDER BY file, id"):
        if is_test_file(r["file"]):
            add(Entry(r["id"], "main", r["file"], r["file"], "program"))

    # Test files that did not parse cleanly may hold tests the index does not have (TEST is not expanded
    # when gtest/gtest.h is missing).
    for r in conn.execute("SELECT path, errors_json FROM files WHERE error_count > 0 ORDER BY path"):
        if is_test_file(r[0]):
            res.unparsed_tests.append(r[0])
            for h in re.findall(r"'([^']+)' file not found", r[1] or ""):
                if h not in res.missing_headers:
                    res.missing_headers.append(h)
    return res


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


def _path(by_id: dict[str, Hit], h: Hit, labels: dict[str, str]) -> list[tuple[str, str]]:
    """From the hit down to the changed thing: [(id, name)], following function ids, so two static
    helpers with the same name are never mixed up. At most 6 steps."""
    steps = [(h.fn.id, h.fn.qualified_name)]
    seen = {h.fn.id}
    via = h.via_id
    while via and via not in seen and len(steps) < 6:
        seen.add(via)
        nxt = by_id.get(via)
        if nxt is None:  # the changed function itself, or a label (a changed declaration, a removed fn)
            steps.append((via, labels.get(via) or via))
            break
        steps.append((nxt.fn.id, nxt.fn.qualified_name))
        via = nxt.via_id
    return steps


_LINK_WORDS = {
    "data": "reads data that {nxt} writes",
    "uses_changed": "uses the changed declaration {nxt}",
    "calls_removed": "still calls the removed {nxt}",
    "via_pointer": "calls {nxt} through a function pointer",
    "virtual": "may call {nxt} through a virtual call",
    "ambiguous": "calls a function named like {nxt}",
}


def _reason(conf: str, steps: list[tuple[str, str]], step_conf: dict[str, str]) -> str:
    """'calls it: a -> b -> c', or, when a step is not a plain call, which step and how."""
    names = [n for _, n in steps]
    chain = " -> ".join(names)
    if conf == "exact":
        return f"calls it: {chain}"
    # Name the first step that is not a direct call (the one that makes this probable).
    for i, (key, n) in enumerate(steps[:-1]):
        link = step_conf.get(key, "exact")
        if link != "exact":
            words = _LINK_WORDS.get(link, "is linked to {nxt} as '" + link + "'").format(nxt=names[i + 1])
            return f"{chain} ({n} {words})"
    return f"{certainty.of_call(conf)[1]}: {chain}"


def _link(
    conn: sqlite3.Connection, res: TestImpact, items: list[_Item], entries: Entries, depth: int
) -> None:
    rank = CONFIDENCE_ORDER.index
    links: dict[tuple[str, str], tuple[TestLink, str]] = {}  # (kind, test) -> (link, confidence)
    any_entries = bool(entries.by_fn)
    for it in items:
        hits, truncated = graph.walk_many(conn, it.starts, "callers", depth, seeds=it.seeds, labels=it.labels)
        res.truncated |= truncated
        by_id = {h.fn.id: h for h in hits}
        # How each hit links to the next one down: the label of that single step.
        step_conf = {h.fn.id: _step_conf(conn, h, it) for h in hits}
        # (function, confidence, steps); steps of None means the entry point itself changed
        reached: list[tuple[Fn, str, list[tuple[str, str]] | None]] = [(f, "exact", None) for f in it.starts]
        reached += [(h.fn, h.confidence, _path(by_id, h, it.labels)) for h in hits]
        found = False
        for fn, conf, steps in reached:
            for e in entries.get(fn.id):
                found = True
                key = (e.kind, e.test)
                if steps is None:
                    why = "its setup changed" if e.role == "setup" else "this test itself changed"
                else:
                    why = ("its setup " if e.role == "setup" else "") + _reason(conf, steps, step_conf)
                level = "exact" if conf == "exact" and not e.guessed else certainty.PROBABLE
                if e.guessed:
                    why += _GUESSED
                cur = links.get(key)
                if cur is None:
                    links[key] = (
                        TestLink(e.test, e.kind, e.file, level, why, [it.name], e.gtest_filter),
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
            why = "no C/C++ test calls it" if any_entries else "no C/C++ test entry point is in the index"
            if truncated and any_entries:
                why += f" within {depth} call steps; more callers exist further up"
            res.not_known.append(NotKnown(it.name, it.where, why))
    order = {certainty.EXACT: 0, certainty.PROBABLE: 1, certainty.UNKNOWN: 2}
    res.tests = sorted(
        (link for link, _ in links.values()),
        key=lambda t: (order[t.level], -len(t.reaches), t.test, t.kind),
    )
    for t in res.tests:
        t.reaches.sort()


def _step_conf(conn: sqlite3.Connection, h: Hit, it: _Item) -> str:
    """The label of the single link from `h` to the function one step closer to the change."""
    if h.distance == 1 and h.fn.id in it.seeds:
        return it.seeds[h.fn.id][0]
    if not h.via_id:
        return h.confidence
    rows = conn.execute(
        "SELECT confidence FROM edges WHERE kind = 'calls' AND src = ? AND dst = ? ORDER BY confidence",
        (h.fn.id, h.via_id),
    ).fetchall()
    labels = sorted({r[0] for r in rows}, key=CONFIDENCE_ORDER.index)
    return labels[0] if labels else h.confidence


def _fn_item(conn: sqlite3.Connection, fn: Fn) -> _Item:
    _, readers = graph.data_coupling(conn, fn.id)
    seeds = {rid: ("data", fn.id, lines) for rid, (_, lines) in sorted(readers.items()) if rid != fn.id}
    return _Item(fn.qualified_name, f"{fn.file}:{fn.line_start}", [fn], seeds, {fn.id: fn.qualified_name})


def _names(items: list[str], n: int = 3) -> str:
    return ", ".join(items[:n]) + (f" and {len(items) - n} more" if len(items) > n else "")


def _base_limits(res: TestImpact, entries: Entries) -> list[str]:
    out = [
        "Static links only: a listed test calls the changed code on some path, but may not run the changed "
        "lines. Tests that reach it through code AeroTrace can't see (scripts, other languages, calls from "
        "outside the index) are not listed."
    ]
    if res.coverage == "not_recorded":
        out.append("Coverage: not recorded, so tests written as scripts (Tcl, Python, shell) are not seen.")
    if res.unindexed_tests:
        n = len(res.unindexed_tests)
        out.append(
            f"{n} test source {'file is' if n == 1 else 'files are'} not in the index, so "
            f"{'its' if n == 1 else 'their'} tests are not seen: {_names(res.unindexed_tests)}."
        )
    if entries.unparsed_tests:
        why = f" (headers not found: {_names(entries.missing_headers)})" if entries.missing_headers else ""
        out.append(
            f"Tests in these files may be missing, because they did not parse cleanly{why}: "
            f"{_names(entries.unparsed_tests)}. Point AeroTrace at your build's compile_commands.json."
        )
    if entries.unrecognised:
        out.append(
            f"{len(entries.unrecognised)} function(s) in test files are passed as pointers in ways not "
            f"recognised as test registrations, so if they are tests they are not listed: "
            f"{_names(entries.unrecognised)}."
        )
    if any(t.reason.endswith(_GUESSED) for t in res.tests):
        out.append(
            "Some tests were found from a registration AeroTrace does not fully recognise (a runner's table "
            "of function pointers, say); they are listed as probable."
        )
    out.append(
        "Test styles read: GoogleTest TEST/TEST_F/TEST_P/TYPED_TEST(_P) and fixture SetUp/TearDown, Unity "
        "RUN_TEST and generated runners, CMocka cmocka_unit_test* and group setup, main in test files. "
        "Other frameworks (Catch2, doctest, Check, CppUTest, ...) are not seen."
    )
    if res.truncated:
        out.append(f"Stopped at depth {res.depth}; tests further up are not listed.")
    return out


def _lines_phrase(lines: list[int]) -> str:
    return f"{'line' if len(lines) == 1 else 'lines'} {diff.ranges(lines)}"


def _outside_why(o: diff.Outside, mode: str) -> str:
    if o.lines and o.other_side and sorted(o.lines) == sorted(o.other_side):
        where = f"{_lines_phrase(o.lines)} (old and new)"
    else:
        bits = []
        if o.lines:
            bits.append(_lines_phrase(o.lines))
        if o.other_side:
            side = "old" if mode == "git" else "new"
            bits.append(f"{_lines_phrase(o.other_side)} ({side} numbering)")
        where = " and ".join(bits)
    return (
        f"{where} changed outside any function (macros, types, initializers); who uses them is not followed"
    )


def for_diff(conn: sqlite3.Connection, root: Path, d: diff.DiffImpact, depth: int = 8) -> TestImpact:
    """Tests for a change already mapped by `diff.analyse`."""
    title = f"compared with {d.base}" if d.mode == "git" else "for a patch that is not applied yet"
    res = TestImpact(title, depth=depth, files=d.files, ignored=d.ignored)
    res.changed_fns = len(d.changed)
    res.removed_fns = len(d.removed)
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
        res.not_known.append(NotKnown(o.file, o.file, _outside_why(o, d.mode), followed=False))
    unfollowed = [
        (d.not_indexed, "not in the index"),
        (d.new_files, "new in the patch; not indexed yet"),
        (d.mismatched, "the patch does not match the file on disk, so it was skipped"),
        (d.unreadable, "its old version could not be read; removed functions are not known"),
        ([f for f in d.deleted if f not in d.unreadable], "deleted; tests that used it are not followed"),
    ]
    for files, why in unfollowed:
        res.not_known += [NotKnown(f, f, why, followed=False) for f in files]
    res.not_known.sort(key=lambda n: (n.where.split(":")[0], _line(n.where), n.what))
    res.limits = _base_limits(res, entries) + [
        n for n in d.blind_spots if not n.startswith("Stopped at depth")
    ]
    return res


def for_function(conn: sqlite3.Connection, root: Path, fn: Fn, depth: int = 8) -> TestImpact:
    """Tests that reach one function, as if it changed."""
    res = TestImpact(f"for {fn.qualified_name} ({fn.where})", depth=depth, changed_fns=1, files=1)
    entries = find_entries(conn, root)
    res.entries = _count(entries)
    res.unindexed_tests = unindexed_test_files(conn, root)
    _link(conn, res, [_fn_item(conn, fn)], entries, depth)
    res.limits = _base_limits(res, entries) + graph.index_limits(conn)
    return res


def _count(entries: Entries) -> Counter:
    tests = {(e.kind, e.test) for es in entries.by_fn.values() for e in es}
    return Counter(k for k, _ in tests)


def _line(where: str) -> int:
    tail = where.rsplit(":", 1)
    return int(tail[1]) if len(tail) == 2 and tail[1].isdigit() else 0


# ---------------------------------------------------------------- how to run them


GTEST_EMPTY_NOTE = (
    "if GoogleTest prints 'did not match any test', the filter is wrong: run the whole binary instead"
)


def commands(res: TestImpact, limit: int = 20) -> list[str]:
    """How to run the listed tests, as far as the index can tell. No binary names are guessed."""
    out: list[str] = []
    gtests: dict[str, list[TestLink]] = {}
    for t in res.tests:
        if t.kind == "gtest":
            gtests.setdefault(t.file, []).append(t)
    for file, ts in sorted(gtests.items()):
        pats = sorted({p for t in ts for p in (t.gtest_filter or t.test).split(":")})
        out.append(
            f"GoogleTest, in the binary built from {file}: --gtest_filter={shlex.quote(':'.join(pats))} "
            f"({GTEST_EMPTY_NOTE})"
        )
    for kind in ("unity", "cmocka", "main"):
        files = sorted({t.file for t in res.tests if t.kind == kind})
        for f in files:
            what = {"unity": "the Unity runner", "cmocka": "the CMocka program", "main": "the test program"}
            out.append(f"{what[kind]} built from {f}")
    return out[:limit]
