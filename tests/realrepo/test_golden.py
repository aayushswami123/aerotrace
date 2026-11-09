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
import json
import os
import re
import sqlite3
import subprocess
from pathlib import Path

import pytest
import repos
from facts import Addr, Broke, Fact, Fn, Use
from facts_data import REDIS as REDIS_DATA_FACTS
from facts_data import SQLITE as SQLITE_DATA_FACTS
from facts_redis import FACTS as REDIS_FACTS
from facts_sqlite import FACTS as SQLITE_FACTS

from aerotrace.analysis import data, diff, graph, render, signature, symbols, testimpact
from aerotrace.index import store
from aerotrace.index.build import build_index, fresh_index
from aerotrace.index.history import Outliner

pytestmark = pytest.mark.realrepo

CASES = [(repos.REDIS, f) for f in REDIS_FACTS + REDIS_DATA_FACTS] + [
    (repos.SQLITE, f) for f in SQLITE_FACTS + SQLITE_DATA_FACTS
]


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


@pytest.fixture(scope="session")
def edited(tmp_path_factory):
    """A fresh copy of the pinned repo with a data fact's edits applied, then indexed."""

    def get(pinned: repos.Pinned, fact: Fact) -> Path:
        root = repos.unpack(pinned, tmp_path_factory.mktemp(f"{pinned.name}-{fact.id}"))
        for e in fact.edits:
            p = root / e.file
            text = p.read_text(errors="replace")
            assert text.count(e.old) == 1, f"{e.file}: the text to replace must appear exactly once"
            p.write_text(text.replace(e.old, e.new, 1))
        build_index(root)
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


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
        cwd=root,
        check=True,
        capture_output=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"},
    )


def git_diff_result(pinned: repos.Pinned, fact: Fact, tmp: Path):
    """Unpack a fresh copy, commit it, make the fact's edits in the working tree, index, run the git diff."""
    root = repos.unpack(pinned, tmp)
    if fact.only_files:
        db = root / "compile_commands.json"
        entries = json.loads(db.read_text())
        keep = [
            e
            for e in entries
            if any(Path(e["directory"], e["file"]).resolve() == (root / f).resolve() for f in fact.only_files)
        ]
        assert len(keep) == len(fact.only_files), "only_files must name compile_commands.json entries"
        db.write_text(json.dumps(keep))
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "--no-verify", "-m", "pinned")
    build_index(root)
    for e in fact.edits:
        p = root / e.file
        p.write_text(p.read_text(errors="replace").replace(e.old, e.new, 1))
    outliner = Outliner(root)
    diffs, desc, old = diff.load_git(root, "HEAD", outliner)
    conn, _ = fresh_index(root, want=diff.wanted(diffs, "git"))
    try:
        return diff.analyse(conn, root, diffs, mode="git", base=desc, old=old, now=outliner.now)
    finally:
        conn.close()


def _diff_problems(res, fact: Fact) -> list[str]:
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


def _check_diff(conn: sqlite3.Connection, root: Path, fact: Fact) -> list[str]:
    diffs = diff.load_patch(root, _patch(root, fact))
    outliner = Outliner(root)
    res = diff.analyse(conn, root, diffs, mode="patch", now=outliner.now, patched=outliner.text)
    return _diff_problems(res, fact)


def _check_calls(want, got, what: str) -> list[str]:
    problems = []
    by = {(s.caller.qualified_name, s.caller.file, s.line): s for s in got}
    for c in want:
        s = by.get((c.caller, c.file, c.line))
        if s is None:
            problems.append(f"missing {what}: {c.caller}@{c.file}:{c.line}")
            continue
        if c.use and s.use != c.use:
            problems.append(f"{c.caller}@{c.file}:{c.line}: result is '{s.use}', expected '{c.use}'")
        if c.detail and c.detail not in s.detail:
            problems.append(
                f"{c.caller}@{c.file}:{c.line}: detail is '{s.detail}', expected '{c.detail}' in it"
            )
        if c.n_args is not None and s.n_args != c.n_args:
            problems.append(f"{c.caller}@{c.file}:{c.line}: passes {s.n_args} arguments, expected {c.n_args}")
        if c.certainty and s.certainty != c.certainty:
            problems.append(
                f"{c.caller}@{c.file}:{c.line}: certainty '{s.certainty}', expected '{c.certainty}'"
            )
    return problems


def _check_signature(conn: sqlite3.Connection, fact: Fact) -> list[str]:
    r = signature.signature_impact(conn, _one(conn, fact.query), depth=0)
    problems = _check_calls(fact.calls, r.sites, "call site")
    problems += _check_calls(fact.pointer_calls, r.pointer_sites, "pointer call")
    if fact.exact:
        want = {(c.caller, c.file, c.line) for c in fact.calls}
        extra = sorted({(s.caller.qualified_name, s.caller.file, s.line) for s in r.sites} - want)
        if extra:
            problems.append(f"call sites the source does not have: {extra[:10]}")
    for a in fact.addresses:
        if not any(x.file == a.file and x.line == a.line and a.how in x.how for x in r.address_sites):
            got = [(x.file, x.line, x.how) for x in r.address_sites]
            problems.append(f"missing address use {a.file}:{a.line} '{a.how}'; got {got[:10]}")
    if fact.returns_void is not None and r.returns_void != fact.returns_void:
        problems.append(f"returns_void is {r.returns_void}, expected {fact.returns_void}")
    callers = {(s.caller.qualified_name, s.caller.file) for s in r.sites + r.pointer_sites}
    problems += [f"false claim: {m} is listed as a call site" for m in fact.must_not if _key(m) in callers]
    if fact.ranked or fact.groups or fact.max_lines or fact.text_must_not or fact.upstream:
        problems += _check_ranking(conn, fact)
    return problems


def _check_ranking(conn: sqlite3.Connection, fact: Fact) -> list[str]:
    """The ranked text answer, asked the way the CLI asks (default caps, `fact.depth` steps further up)."""
    r = signature.signature_impact(conn, _one(conn, fact.query), depth=fact.depth)
    text = render.signature_text(r)
    problems = []
    rows = text.splitlines()
    pos = []
    for caller, file, line in fact.ranked:
        at = [i for i, row in enumerate(rows) if row.startswith(f"  - {caller}  {file}:{line}")]
        if not at:
            problems.append(f"ranked call site {caller}@{file}:{line} is not in the default answer")
        pos.append(at[0] if at else -1)
    if -1 not in pos and pos != sorted(pos):
        problems.append(f"call sites are not in the expected order: {list(fact.ranked)}")
    for g in fact.groups:
        name, file = g.entry.split("@")
        found = [
            x
            for x in r.groups
            if x.kind == "pointer" and x.entry.qualified_name == name and x.entry.file == file
        ]
        if not found or g.line not in found[0].lines:
            problems.append(f"no group for the pointer call in {g.entry} at line {g.line}")
            continue
        members = {(h.fn.qualified_name, h.fn.file): h for h in found[0].members}
        for m in g.members:
            h = members.get(_key(m))
            if h is None:
                problems.append(f"{m} is not in the group of {g.entry}")
            elif m.confidence and h.confidence != m.confidence:
                problems.append(f"{m} is '{h.confidence}' in the group of {g.entry}")
    hits = {(h.fn.qualified_name, h.fn.file): h for h in r.upstream}
    for m in fact.upstream:
        h = hits.get(_key(m))
        if h is None:
            problems.append(f"{m} is not a caller further up")
        elif m.confidence and h.confidence != m.confidence:
            problems.append(f"{m} is '{h.confidence}' further up")
    want_ptr = sum(1 for m in fact.upstream if m.confidence == "via_pointer")
    said = re.search(r"(\d+) of them reached through a pointer call on the way", text)
    if fact.upstream and (said is None or int(said.group(1)) < want_ptr):
        problems.append(f"the summary counts fewer than {want_ptr} callers reached through a pointer call")
    # a must_not with a confidence forbids only that label ("never exact"); without one, any listing
    in_groups = {(h.fn.qualified_name, h.fn.file): h for x in r.groups for h in x.members}
    problems += [
        f"false claim: {m} is a caller further up"
        for m in fact.must_not
        if _key(m) in in_groups and (m.confidence is None or in_groups[_key(m)].confidence == m.confidence)
    ]
    if fact.max_lines is not None and len(rows) > fact.max_lines:
        problems.append(f"the default answer has {len(rows)} lines, more than {fact.max_lines}")
    head = text.split("Limits of this answer")[0]
    problems += [f"the answer says '{w}'" for w in fact.text_must_not if w in head]
    if r.sites:
        start = next(i for i, row in enumerate(rows) if row.startswith("Call sites ("))
        listed = 0
        for row in rows[start + 1 :]:
            if not row.startswith("  - "):
                break
            listed += 1
        m = re.search(r"\.\.\. and (\d+) more call sites", text)
        if listed + (int(m.group(1)) if m else 0) != len(r.sites):
            problems.append(f"{listed} call sites shown + 'N more' is not {len(r.sites)}")
    return problems


def _check_data(conn: sqlite3.Connection, fact: Fact) -> list[str]:
    found = data.find_variables(conn, fact.query) or data.gone_fields(conn, fact.query)
    if len(found) != 1:
        return [
            f"'{fact.query}' should name exactly one variable; AeroTrace found {[v.shown for v in found]}"
        ]
    writer = _one(conn, fact.data_from).id if fact.data_from else None
    r = data.data_users_of(conn, found[0], fact.depth if fact.must else None, writer)
    got = {(u.fn.qualified_name, u.fn.file): u for u in r.uses}
    problems = []

    def lines_of(u, kind):
        return u.writes if kind == "writes" else u.reads if kind == "reads" else u.reads + u.writes

    def matches(w: Use) -> bool:
        u = got.get((w.fn, w.file))
        if u is None:
            return False
        ls = lines_of(u, w.kind)
        return bool(ls) and (w.line is None or w.line in ls)

    for w in fact.uses:
        u = got.get((w.fn, w.file))
        if u is None:
            problems.append(f"missing user: {w.fn}@{w.file}")
        elif not matches(w):
            problems.append(
                f"{w.fn}@{w.file}: expected {w.kind or 'a use'}{f' at line {w.line}' if w.line else ''}; "
                f"got writes {u.writes[:8]}, reads {u.reads[:8]}"
            )
        elif w.certainty and u.certainty != w.certainty:
            problems.append(f"{w.fn}@{w.file}: certainty '{u.certainty}', expected '{w.certainty}'")
    if fact.exact:
        extra = sorted(set(got) - {(w.fn, w.file) for w in fact.uses})
        if extra:
            problems.append(f"users the source does not have: {extra[:10]}")
    if fact.exact_writers:
        writers = {k for k, u in got.items() if u.writes}
        want = {(w.fn, w.file) for w in fact.uses if w.kind == "writes"}
        if writers != want:
            problems.append(f"writers are {sorted(writers)}, expected exactly {sorted(want)}")
    for i in fact.inits:
        if not any(
            x.variable.shown == i.variable and x.file == i.file and x.line == i.line for x in r.initializers
        ):
            have = sorted({(x.variable.shown, x.file, x.line) for x in r.initializers})
            problems.append(f"missing initializer {i.variable} at {i.file}:{i.line}; got {have[:10]}")
    for a in fact.escapes:
        if not any(e.fn.file == a.file and e.line == a.line and a.how in e.detail for e in r.escapes):
            have = [(e.fn.file, e.line, e.detail) for e in r.escapes]
            problems.append(f"missing escape {a.file}:{a.line} '{a.how}'; got {have[:10]}")
    names = {g.shown for g in r.by_position}
    problems += [
        f"{g} is not listed as a global that may set it by position"
        for g in fact.by_position
        if g not in names
    ]
    for b in fact.broken:
        hit = [x for x in r.broken if x.file == b.file and x.line == b.line]
        if not hit:
            have = [(x.file, x.line) for x in r.broken]
            problems.append(f"missing place that does not compile: {b.file}:{b.line}; got {have[:12]}")
        elif not any((x.fn.qualified_name if x.fn else "") == b.fn for x in hit):  # "" = file scope
            problems.append(f"{b.file}:{b.line} is not placed in {b.fn}")
        elif b.certainty and not any(x.certainty == b.certainty for x in hit):
            problems.append(
                f"{b.file}:{b.line}: certainty {[x.certainty for x in hit]}, expected {b.certainty}"
            )
    problems += _check_hits(
        Fact(id="", why="", kind=""),  # Fn in must_not means "not a user" (checked below), not "no caller"
        r.upstream,
        list(fact.must),
        "callers further up",
    )
    for m in fact.must_not:
        if isinstance(m, Use) and matches(m):
            what = f"{m.kind or 'a user'}{f' at line {m.line}' if m.line else ''}"
            problems.append(f"false claim: {m.fn}@{m.file} is listed as {what}")
        elif isinstance(m, Fn) and _key(m) in got:
            problems.append(f"false claim: {m} is listed as a user")
        elif isinstance(m, Addr) and any(
            e.fn.file == m.file and e.line == m.line and m.how in e.detail for e in r.escapes
        ):
            problems.append(f"false claim: an escape at {m.file}:{m.line} '{m.how}'")
        elif isinstance(m, Broke) and any(x.file == m.file and x.line == m.line for x in r.broken):
            problems.append(f"false claim: {m.file}:{m.line} is listed as not compiling")
    return problems


def _check_tests(conn: sqlite3.Connection, root: Path, fact: Fact) -> list[str]:
    res = testimpact.for_function(conn, root, _one(conn, fact.query))
    problems = []
    got = {t.test for t in res.tests}
    missing = sorted(set(fact.tests) - got)
    if missing:
        problems.append(f"tests that should be listed are missing: {missing}")
    if fact.exact and got - set(fact.tests):
        problems.append(f"tests listed that the source does not link: {sorted(got - set(fact.tests))[:10]}")
    unknown = {n.what for n in res.not_known}
    problems += [f"{n} should be under 'Not known'" for n in fact.not_known if n not in unknown]
    if res.no_test_reaches:
        problems.append(f"'No test reaches' from static links alone: {[n.what for n in res.no_test_reaches]}")
    entries = testimpact.find_entries(conn, root)
    names = symbols.get_many(conn, list(entries.by_fn))
    as_entry = {(names[i].qualified_name, names[i].file) for i in entries.by_fn if i in names}
    problems += [f"false claim: {m} is a test entry point" for m in fact.must_not if _key(m) in as_entry]
    text = render.tests_text(res)
    if re.search(r"no tests? (are |is )?affected|\b0 tests\b", text, re.I):
        problems.append("the answer claims there are no tests")
    if "No tests to list" in text and "This does not mean no test runs this code" not in text:
        problems.append("'No tests to list' without saying it does not mean the code is untested")
    return problems


def _check_check(conn: sqlite3.Connection, root: Path, fact: Fact) -> list[str]:
    from aerotrace.analysis import check
    from aerotrace.ui import schema

    diffs = diff.load_patch(root, _patch(root, fact))
    res = check.run(conn, root, diffs, mode="patch", outliner=Outliner(root))
    data = check.to_contract(conn, res, "aerotrace golden", cap=100000)
    problems = []
    try:
        schema.check(data)
    except schema.ContractError as e:
        problems.append(f"the answer does not follow the UI contract: {e}")
    nodes = {(n["name"], n["file"]): n for n in data["nodes"]}
    broken = {k for k, n in nodes.items() if n["status"] == "breaks"}
    for f in fact.breaks:
        n = nodes.get(_key(f))
        if n is None or n["status"] != "breaks":
            problems.append(f"missing break: {f.name}@{f.file} ({n['status'] if n else 'not listed'})")
        elif f.confidence and n["certainty"] != f.confidence:
            problems.append(f"{f.name}@{f.file}: break is {n['certainty']}, expected {f.confidence}")
    for f in fact.affected_only:
        n = nodes.get(_key(f))
        if n is None or n["status"] != "affected":
            problems.append(
                f"{f.name}@{f.file} should be affected (not a break), got {n['status'] if n else 'nothing'}"
            )
    if fact.no_breaks and broken:
        problems.append(f"false breaks: {sorted(broken)[:10]}")
    if fact.breaks and fact.exact and broken - {_key(f) for f in fact.breaks}:
        problems.append(f"extra breaks: {sorted(broken - {_key(f) for f in fact.breaks})[:10]}")
    s = data["summary"]
    if fact.not_clear and not (
        s["breaks_exact"] or s["breaks_probable"] or s["unknown"] or s["not_followed"]
    ):
        problems.append("the answer is an all-clear")
    for m in fact.must_not:
        if _key(m) in broken:
            problems.append(f"false claim: {m.name}@{m.file} is listed as a break")
    if fact.no_outside and data["change"]["not_followed"]["outside"]:
        problems.append(f"lines reported as 'could not follow': {data['change']['not_followed']['outside']}")
    return problems


def answer_problems(root: Path, fact: Fact) -> list[str]:
    conn = store.connect(root)
    try:
        if fact.kind == "check":
            return _check_check(conn, root, fact)
        if fact.kind == "data":
            return _check_data(conn, fact)
        if fact.kind == "diff":
            return _check_diff(conn, root, fact)
        if fact.kind == "tests":
            return _check_tests(conn, root, fact)
        if fact.kind == "signature":
            return _check_signature(conn, fact)
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
def test_fact(indexed, edited, pinned, fact, tmp_path):
    if fact.kind == "git_diff":
        problems = _diff_problems(git_diff_result(pinned, fact, tmp_path), fact)
    elif fact.kind == "data" and fact.edits:
        problems = answer_problems(edited(pinned, fact), fact)
    else:
        problems = answer_problems(indexed(pinned), fact)
    assert not problems, f"{fact.id}: {fact.why}\n  - " + "\n  - ".join(problems)
