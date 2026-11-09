"""Calls the parser could not link, or linked wrongly: they must show up as callers or as UNKNOWN, never
as a quiet "no callers" (#33, #32, #49, #51, #31, #21, #39, #40)."""

import json
import subprocess

import pytest

from aerotrace.analysis import check, diff, graph, render, signature, symbols
from aerotrace.cli import main
from aerotrace.index import store
from aerotrace.index.build import build_index, fresh_index
from aerotrace.index.history import Outliner


def one(conn, query):
    [f] = symbols.find(conn, query)
    return f


@pytest.fixture
def gaps(repo_factory):
    root = repo_factory("call_gaps")
    build_index(root, jobs=1)
    conn = store.connect(root)
    yield conn
    conn.close()


# ------------------------------------------------ #33: calls in templates that depend on T


def test_template_calls_that_depend_on_t_are_unknown_not_safe(gaps):
    run = one(gaps, "D::run")
    w = graph.walk(gaps, run, "callers", 2)
    assert "direct" in {h.fn.name for h in w.hits}
    # tcall<D> calls D::run, but libclang shows no instantiation: the answer must say so
    [note] = [n for n in w.unknown if "named run" in n]
    assert note.startswith("UNKNOWN (#33)") and "tcall (src/templates.cpp:13)" in note
    assert "UNKNOWN (#33)" in render.walk_text(w)
    assert render.walk_json(w)["unknown"] == w.unknown
    assert any("tcall" in n for n in graph.impact(gaps, run).blind_spots)
    assert any("tcall" in n for n in signature.signature_impact(gaps, run).blind_spots)
    # T::sm(1) and an unqualified call free_fn(t) too
    assert any(
        "tstatic (src/templates.cpp:14)" in n
        for n in graph.walk(gaps, one(gaps, "D::sm"), "callers", 1).unknown
    )
    assert any(
        "tfree (src/templates.cpp:15)" in n
        for n in graph.walk(gaps, one(gaps, "free_fn"), "callers", 1).unknown
    )
    # must not: a function no template calls gets no such note
    assert graph.walk(gaps, one(gaps, "lonely"), "callers", 1).unknown == []


# ------------------------------------------------ #32: an override that stops overriding


def git(root, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


def git_diff(root, monkeypatch, rel, old, new):
    """Commit the fixture, index it, then change `old` to `new` in `rel` and run diff in git mode."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "--no-verify", "-m", "init")
    build_index(root, jobs=1)
    p = root / rel
    assert old in p.read_text()
    p.write_text(p.read_text().replace(old, new))
    outliner = Outliner(root)
    diffs, desc, old_outlines = diff.load_git(root, "HEAD", outliner)
    conn, _ = fresh_index(root, want=diff.wanted(diffs, "git"))
    res = diff.analyse(conn, root, diffs, mode="git", base=desc, old=old_outlines, now=outliner.now)
    conn.close()
    return res


@pytest.mark.parametrize("override", ["override", ""])
def test_override_that_stops_overriding_keeps_dispatching_callers(repo_factory, monkeypatch, override):
    root = repo_factory("call_gaps")
    old = "int area(int u) override { return 3 * u; }"
    res = git_diff(
        root, monkeypatch, "src/virt.cpp", old, f"int area(int u, int v) {override} {{ return 3 * u; }}"
    )
    assert [c.fn.qualified_name for c in res.changed] == ["Big::area"]
    got = {h.fn.name: (h.distance, h.confidence) for h in res.affected}
    # b->area(1) could reach Big::area before the change, and can't now
    assert got["use_area"] == (1, "virtual") and got["top"] == (2, "virtual")
    [hit] = [h for h in res.affected if h.fn.name == "use_area"]
    assert hit.via == "Box::area (which Big::area may no longer override)"


def test_body_change_of_an_override_adds_no_extra_callers(repo_factory, monkeypatch):
    root = repo_factory("call_gaps")
    res = git_diff(root, monkeypatch, "src/virt.cpp", "return 3 * u;", "return 4 * u;")
    got = {h.fn.name: h.confidence for h in res.affected}
    # must not: on_box calls Box::area by name (no dispatch), it never reached Big::area
    assert got.get("use_area") == "virtual" and "on_box" not in got


# ------------------------------------------------ #51: CRLF files


def crlf_repo(repo_factory):
    root = repo_factory("testimpact")
    p = root / "src/calc.c"
    p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    build_index(root, jobs=1)
    lines = p.read_bytes().split(b"\r\n")
    n = lines.index(b"int add(int a, int b) { return a + b; }") + 1
    patch = (
        b"--- a/src/calc.c\r\n+++ b/src/calc.c\r\n"
        + f"@@ -{n} +{n} @@\r\n".encode()
        + b"-int add(int a, int b) { return a + b; }\r\n"
        + b"+int add(int a, int b, int c) { return a + b + c; }\r\n"
    )
    return root, patch


@pytest.mark.parametrize("ends", ["crlf", "lf"])
def test_crlf_patch_matches_the_changed_function(repo_factory, ends):
    root, patch = crlf_repo(repo_factory)
    if ends == "lf":  # an agent writes the patch with LF line ends for a CRLF file
        patch = patch.replace(b"\r\n", b"\n")
    diffs = diff.load_patch(root, patch.decode())
    conn, _ = fresh_index(root, want=diff.wanted(diffs, "patch"))
    res = check.run(conn, root, diffs, mode="patch", outliner=Outliner(root))
    c = check.to_contract(conn, res, "aerotrace test")
    conn.close()
    assert c["change"]["not_followed"]["outside"] == []
    assert [n["name"] for n in c["nodes"] if n["distance"] == 0] == ["add"]
    assert c["summary"]["breaks_exact"] >= 4


def test_cli_check_keeps_crlf_in_the_patch_file(repo_factory, tmp_path, capsys):
    root, patch = crlf_repo(repo_factory)
    (tmp_path / "p.diff").write_bytes(patch)
    main(["check", "--root", str(root), "--patch", str(tmp_path / "p.diff"), "--json"])
    c = json.loads(capsys.readouterr().out)
    assert c["change"]["not_followed"]["outside"] == [] and c["change"]["not_followed"]["mismatched"] == []
    assert [n["name"] for n in c["nodes"] if n["distance"] == 0] == ["add"]


# ------------------------------------------------ #31: an overload set used as a value in a template


def test_overload_used_as_a_value_in_a_template_is_address_taken(gaps):
    rows = gaps.execute(
        """SELECT f.signature FROM edges e JOIN functions s ON s.id = e.src JOIN functions f ON f.id = e.dst
           WHERE s.name = 'ptrcall' AND e.kind = 'calls' AND e.confidence = 'via_pointer'"""
    )
    # g1<int (*)(int)> stores ov(int) in p: the call through int (*)(int) may reach it
    assert "int ov(int)" in {r[0] for r in rows}
    sites = gaps.execute(
        """SELECT f.signature, a.line, a.how FROM address_sites a JOIN functions f ON f.id = a.function_id
           WHERE f.name = 'ov' ORDER BY 1"""
    )
    assert [tuple(r) for r in sites] == [
        ("int ov(int)", 23, "stored in p"),
        ("int ov(int, int)", 23, "stored in p"),
    ]
    # must not: free_fn(t) in a template is a call, not a use as a value
    sql = "SELECT f.name FROM address_taken a JOIN functions f ON f.id = a.function_id"
    assert "free_fn" not in {r[0] for r in gaps.execute(sql)}


# ------------------------------------------------ #21, #39: broken constructor and operator() calls


def test_broken_constructor_and_operator_calls_are_calls(gaps):
    rows = gaps.execute(
        """SELECT s.name, d.qualified_name || ' ' || d.signature, e.confidence, e.line FROM edges e
           JOIN functions s ON s.id = e.src JOIN functions d ON d.id = e.dst
           WHERE e.kind = 'calls' AND s.file = 'src/ctor.cpp'"""
    )
    got = {tuple(r) for r in rows}
    for caller, line in (("b6", 13), ("b7", 14), ("b8", 15)):
        # no constructor matches: it may be meant for either one
        assert (caller, "ns::K::K K(int)", "ambiguous", line) in got, caller
        assert (caller, "ns::K::K K(const char *)", "ambiguous", line) in got, caller
    assert ("use", "F::operator() int operator()(int)", "exact", 16) in got
    assert ("ok", "F::operator() int operator()(int)", "exact", 17) in got
    assert ("good", "ns::K::K K(int)", "exact", 18) in got
    # must not: good calls K(int) only
    assert ("good", "ns::K::K K(const char *)", "ambiguous", 18) not in got
    # G has no constructor in the repo: never silent, the call stays a blind spot
    sql = """SELECT b.kind, b.detail FROM blind_spots b JOIN functions f ON f.id = b.function_id
             WHERE f.name = 'none'"""
    assert [tuple(r) for r in gaps.execute(sql)] == [
        ("unresolved_call", "G: a call that does not compile; no constructor of G was found in the repo")
    ]


# ------------------------------------------------ #40: a header function compiled as C and as C++


def test_header_function_compiled_as_c_and_cpp_gives_both_copies(gaps):
    by_line = symbols.find(gaps, "include/ring.h:7")
    assert sorted(f.language for f in by_line) == ["C", "C++"]
    assert {f.qualified_name for f in by_line} == {"ring_full"}
    # the choices can be told apart, and each one can be asked for
    text = render.candidates_text("include/ring.h:7", by_line)
    assert "compiled as C)" in text and "compiled as C++)" in text
    callers = {}
    for f in by_line:
        assert f.id in text
        [again] = symbols.find(gaps, f.id)
        callers[f.language] = {h.fn.name for h in graph.walk(gaps, again, "callers", 1).hits}
    assert callers == {"C": {"c_push"}, "C++": {"cpp_push"}}
    # must not: a plain function on a line still gives one answer
    assert [f.name for f in symbols.find(gaps, "src/ring.c:3")] == ["c_push"]


# ------------------------------------------------ #12: a comparison; #22: (*f)(x)


def test_comparing_a_function_does_not_take_its_address(gaps):
    taken = {
        r[0]
        for r in gaps.execute("SELECT f.name FROM address_taken a JOIN functions f ON f.id = a.function_id")
    }
    assert "stored" in taken and "only_compared" not in taken
    reached = {h.fn.name for h in graph.walk(gaps, one(gaps, "run"), "callees", 1).hits}
    # must not: p(1) can't reach only_compared, which is never stored anywhere
    assert "stored" in reached and "only_compared" not in reached
    # where it is named is still recorded
    sql = """SELECT a.line, a.how FROM address_sites a JOIN functions f ON f.id = a.function_id
             WHERE f.name = 'only_compared' ORDER BY 1"""
    assert [tuple(r) for r in gaps.execute(sql)] == [(10, "compared"), (11, "checked")]


def test_call_through_star_or_amp_of_a_function_is_direct(gaps):
    hits = {(h.fn.name, h.confidence) for h in graph.walk(gaps, one(gaps, "two"), "callers", 1).hits}
    assert hits == {("good_deref", "exact"), ("good_addr", "exact")}
    taken = {
        r[0]
        for r in gaps.execute("SELECT f.name FROM address_taken a JOIN functions f ON f.id = a.function_id")
    }
    assert "two" not in taken
