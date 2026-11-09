"""Calls the parser could not link, or linked wrongly: they must show up as callers or as UNKNOWN, never
as a quiet "no callers" (#33, #32, #49, #51, #31, #21, #39, #40)."""

import subprocess

import pytest

from aerotrace.analysis import diff, graph, render, signature, symbols
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
