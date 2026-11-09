"""Golden tests on pinned real repos. Run with `uv run pytest -m realrepo` (CI runs them on every PR).

Two tests per fact:
- test_proof: the source lines that prove the fact really are in the pinned source. This checks the
  person, not the tool: if a proof is wrong, the fact is wrong.
- test_fact: AeroTrace's answer has everything in `must` and nothing in `must_not`.

Facts with `known_bug` are expected to fail today (strict xfail). If a change fixes one, the test reports
"XPASS" and fails, so the mark gets removed and the fix is counted.
"""

from __future__ import annotations

import difflib
import re
import sqlite3
from pathlib import Path

import pytest
import repos
from facts import Fact, Fn
from facts_redis import FACTS as REDIS_FACTS
from facts_sqlite import FACTS as SQLITE_FACTS

from aerotrace.analysis import diff, graph, symbols
from aerotrace.index import store
from aerotrace.index.build import build_index
from aerotrace.index.history import Outliner

pytestmark = pytest.mark.realrepo

CASES = [(repos.REDIS, f) for f in REDIS_FACTS] + [(repos.SQLITE, f) for f in SQLITE_FACTS]


def _param(pinned: repos.Pinned, fact: Fact, check_bugs: bool):
    marks = [pytest.mark.xfail(strict=True, reason=fact.known_bug)] if check_bugs and fact.known_bug else []
    return pytest.param(pinned, fact, id=fact.id, marks=marks)


# ---------------------------------------------------------------- the pinned repos, unpacked and indexed once


@pytest.fixture(scope="session")
def sources(tmp_path_factory):
    roots: dict[str, Path] = {}

    def get(pinned: repos.Pinned) -> Path:
        if pinned.name not in roots:
            roots[pinned.name] = repos.unpack(pinned, tmp_path_factory.mktemp(pinned.name))
        return roots[pinned.name]

    return get


@pytest.fixture(scope="session")
def indexed(sources):
    done: dict[str, Path] = {}

    def get(pinned: repos.Pinned) -> Path:
        root = sources(pinned)
        if pinned.name not in done:
            build_index(root)
            done[pinned.name] = root
        return root

    return get


# ---------------------------------------------------------------- proof lines


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


@pytest.mark.parametrize("pinned, fact", [_param(p, f, check_bugs=False) for p, f in CASES])
def test_proof(sources, pinned, fact):
    root = sources(pinned)
    assert fact.proof, "every fact needs proof lines"
    for ln in fact.proof:
        lines = (root / ln.file).read_text(errors="replace").split("\n")
        got = lines[ln.line - 1] if 0 < ln.line <= len(lines) else "<past the end of the file>"
        assert _norm(ln.text) in _norm(got), f"{ln.file}:{ln.line} is {got!r}, the proof says {ln.text!r}"
    for e in fact.edits:
        n = (root / e.file).read_text(errors="replace").count(e.old)
        assert n == 1, f"edit text appears {n} times in {e.file}; it must appear exactly once"


# ---------------------------------------------------------------- asking AeroTrace


def _one(conn: sqlite3.Connection, query: str) -> symbols.Fn:
    found = symbols.find(conn, query)
    assert len(found) == 1, f"'{query}' should name exactly one function; AeroTrace found {len(found)}"
    return found[0]


def _key(f: Fn) -> tuple[str, str]:
    return (f.name, f.file)


def _hits_by_key(hits) -> dict[tuple[str, str], graph.Hit]:
    out: dict[tuple[str, str], graph.Hit] = {}
    for h in hits:
        out.setdefault((h.fn.qualified_name, h.fn.file), h)
    return out


def _check_hits(fact: Fact, hits, must: list[Fn], what: str) -> list[str]:
    """Problems with a list of hits (callers, callees or affected functions), in plain words."""
    problems = []
    by = _hits_by_key(hits)
    for m in must:
        h = by.get(_key(m))
        if h is None:
            problems.append(f"missing: {m} should be among the {what}")
        elif m.confidence and h.confidence != m.confidence:
            problems.append(f"wrong label: {m.name} is [{h.confidence}], expected [{m.confidence}]")
        elif m.distance and h.distance != m.distance:
            problems.append(f"wrong distance: {m.name} is at {h.distance}, expected {m.distance}")
    for m in fact.must_not:
        h = by.get(_key(m))
        if h is not None:
            problems.append(
                f"false claim: {m} is listed among the {what} [{h.confidence}, distance {h.distance}]"
            )
    if fact.exact:
        near = {k for k, h in by.items() if h.distance == 1}
        extra = sorted(near - {_key(m) for m in must})
        if extra:
            problems.append(
                f"extra {what} that the source does not have: {', '.join(f'{n}@{f}' for n, f in extra[:10])}"
            )
    return problems


def _reads(conn: sqlite3.Connection, fid: str) -> list[str]:
    return [
        r[0]
        for r in conn.execute(
            """SELECT DISTINCT v.qualified_name FROM edges e JOIN variables v ON v.id = e.dst
               WHERE e.src = ? AND e.kind = 'reads' ORDER BY 1""",
            (fid,),
        )
    ]


def _patch(root: Path, fact: Fact) -> str:
    """The fact's edits as one unified diff (like `git diff`), without touching the files."""
    out = []
    by_file: dict[str, str] = {}
    for e in fact.edits:
        before = by_file.get(e.file) or (root / e.file).read_text(errors="replace")
        by_file[e.file] = before.replace(e.old, e.new, 1)
        if e.file not in [x for x, _ in out]:
            out.append((e.file, before))
    text = ""
    for f, before in out:
        text += "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True), by_file[f].splitlines(keepends=True), f"a/{f}", f"b/{f}"
            )
        )
    return text


def _check_diff(conn: sqlite3.Connection, root: Path, fact: Fact) -> list[str]:
    diffs = diff.load_patch(root, _patch(root, fact))
    res = diff.analyse(conn, root, diffs, mode="patch", now=Outliner(root).now)
    problems = []
    if res.mismatched or res.not_indexed:
        problems.append(f"the patch did not map: mismatched {res.mismatched}, not indexed {res.not_indexed}")
    got = {f"{c.fn.qualified_name}@{c.fn.file}": c.state for c in res.changed}
    if set(got) != set(fact.changed):
        problems.append(
            f"changed functions are {sorted(got) or 'none'}, expected {sorted(fact.changed) or 'none'}"
        )
    for k, state in fact.changed_state.items():
        if got.get(k) not in (None, state):
            problems.append(f"{k} is marked '{got[k]}', expected '{state}'")
    decls = sorted(v.name for v in res.changed_vars)
    if decls != sorted(fact.changed_decls):
        problems.append(
            f"changed declarations are {decls or 'none'}, expected {sorted(fact.changed_decls) or 'none'}"
        )
    if not fact.changed and not fact.changed_decls and res.affected:
        problems.append(f"nothing changed, but {len(res.affected)} functions are listed as affected")
    if fact.no_outside and res.outside:
        problems.append(
            f"lines reported as 'could not follow': {[(o.file, o.lines, o.other_side) for o in res.outside]}"
        )
    for m in fact.must_not:
        if f"{m.name}@{m.file}" in got:
            problems.append(f"false claim: {m} is listed as changed")
    return problems + _check_hits(fact, res.affected, list(fact.must), "affected functions")


def answer_problems(root: Path, fact: Fact) -> list[str]:
    conn = store.connect(root)
    try:
        if fact.kind == "diff":
            return _check_diff(conn, root, fact)
        if fact.kind == "find":
            found = {(f.qualified_name, f.file) for f in symbols.find(conn, fact.query)}
            problems = [f"missing: {m}" for m in fact.must if _key(m) not in found]
            problems += [f"false claim: {m} is in the index" for m in fact.must_not if _key(m) in found]
            if fact.exact and found != {_key(m) for m in fact.must}:
                problems.append(
                    f"find '{fact.query}' gives {sorted(found)}, expected {[str(m) for m in fact.must]}"
                )
            return problems
        fn = _one(conn, fact.query)
        if fact.kind in ("callers", "callees"):
            must = list(fact.must) + (fact.must_from_source(root) if fact.must_from_source else [])
            return _check_hits(fact, graph.walk(conn, fn, fact.kind, fact.depth).hits, must, fact.kind)
        if fact.kind == "impact":
            return _check_hits(
                fact, graph.impact(conn, fn, fact.depth).callers.hits, list(fact.must), "affected functions"
            )
        if fact.kind in ("writes", "reads"):
            got = set(graph.data_coupling(conn, fn.id)[0] if fact.kind == "writes" else _reads(conn, fn.id))
            problems = [
                f"missing: {fn.qualified_name} should {fact.kind[:-1]} {v}" for v in fact.must if v not in got
            ]
            problems += [
                f"false claim: {fn.qualified_name} is said to {fact.kind[:-1]} {v}"
                for v in fact.must_not
                if v in got
            ]
            if fact.exact and got != set(fact.must):
                problems.append(f"{fact.kind} are {sorted(got)}, expected exactly {sorted(fact.must)}")
            return problems
        raise AssertionError(f"unknown fact kind {fact.kind}")
    finally:
        conn.close()


# Files allowed to have parse errors, checked by hand. Anything else means the flags or headers broke,
# which silently drops links (on Linux, a clang/libclang version mismatch once hid every C11 atomic).
EXPECTED_PARSE_ERRORS = {
    # release.c includes "release.h", which Redis generates during its build (src/mkreleasehdr.sh);
    # the release tarball does not contain it.
    "redis": {"src/release.c"},
    "sqlite": set(),
}


@pytest.mark.parametrize("pinned", repos.ALL, ids=[p.name for p in repos.ALL])
def test_only_expected_files_have_parse_errors(indexed, pinned):
    conn = store.connect(indexed(pinned))
    try:
        bad = {r[0]: r[1] for r in conn.execute("SELECT path, errors_json FROM files WHERE error_count > 0")}
    finally:
        conn.close()
    unexpected = {f: e for f, e in bad.items() if f not in EXPECTED_PARSE_ERRORS[pinned.name]}
    assert not unexpected, f"parse errors in files that should parse cleanly: {unexpected}"


@pytest.mark.parametrize("pinned, fact", [_param(p, f, check_bugs=True) for p, f in CASES])
def test_fact(indexed, pinned, fact):
    problems = answer_problems(indexed(pinned), fact)
    assert not problems, f"{fact.id}: {fact.why}\n  - " + "\n  - ".join(problems)
