"""Calls the parser could not link, or linked wrongly: they must show up as callers or as UNKNOWN, never
as a quiet "no callers" (#33, #32, #49, #51, #31, #21, #39, #40)."""

import pytest

from aerotrace.analysis import graph, render, signature, symbols
from aerotrace.index import store
from aerotrace.index.build import build_index


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
