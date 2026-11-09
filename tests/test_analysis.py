import pytest

from aerotrace.analysis import graph, render, symbols
from aerotrace.index import store
from aerotrace.index.build import build_index


@pytest.fixture
def c_conn(repo_factory):
    root = repo_factory("c_multi")
    build_index(root, jobs=1)
    return store.connect(root)


@pytest.fixture
def cpp_conn(repo_factory):
    root = repo_factory("cpp_virtual")
    build_index(root, jobs=1)
    return store.connect(root)


def one(conn, query):
    fns = symbols.find(conn, query)
    assert len(fns) == 1, fns
    return fns[0]


def summary(w):
    return [(h.distance, h.fn.qualified_name, h.fn.file, h.confidence) for h in w.hits]


# ---------------------------------------------------------------- finding functions


def test_a_shared_name_returns_every_match(c_conn):
    assert [f.file for f in symbols.find(c_conn, "main")] == ["prog_a/main.c", "prog_b/main.c"]
    assert one(c_conn, "main@prog_b/main.c").file == "prog_b/main.c"


def test_file_and_line_finds_the_enclosing_function(c_conn):
    assert one(c_conn, "lib/shared.c:6").name == "add"
    # two files are called util.c: both are returned, never just the first
    assert [f.file for f in symbols.find(c_conn, "util.c:4")] == ["prog_a/util.c", "prog_b/util.c"]


def test_search_matches_part_of_a_name(c_conn):
    assert [f.name for f in symbols.search(c_conn, "util_run")] == ["util_run", "util_run_b"]
    assert symbols.search(c_conn, "%") == []  # wildcards are taken literally


# ---------------------------------------------------------------- callers and callees


def test_callers_follow_function_pointers_with_a_label(c_conn):
    w = graph.walk(c_conn, one(c_conn, "add"), "callers", 5)
    assert summary(w) == [
        (1, "on_a", "lib/table.c", "exact"),
        (1, "on_b", "lib/table.c", "exact"),
        (1, "main", "prog_a/main.c", "exact"),
        (2, "dispatch", "lib/table.c", "via_pointer"),
        (3, "main", "prog_b/main.c", "via_pointer"),
    ]


def test_callees_reach_through_pointers_and_stay_in_their_program(c_conn):
    w = graph.walk(c_conn, one(c_conn, "main@prog_b/main.c"), "callees", 5)
    names = {(h.fn.qualified_name, h.fn.file, h.confidence) for h in w.hits}
    assert ("on_a", "lib/table.c", "via_pointer") in names
    assert ("bump", "prog_b/util.c", "exact") in names
    assert not any(f == "prog_a/util.c" for _, f, _ in names)


def test_depth_limit_is_reported(c_conn):
    w = graph.walk(c_conn, one(c_conn, "add"), "callers", 2)
    assert max(h.distance for h in w.hits) == 2
    assert w.truncated_at_depth


def test_virtual_callers_are_labelled(cpp_conn):
    w = graph.walk(cpp_conn, one(cpp_conn, "Square::area"), "callers", 3)
    assert summary(w) == [(1, "fixed", "shapes.cpp", "exact"), (1, "total", "shapes.cpp", "virtual")]


# ---------------------------------------------------------------- blast radius


def test_impact_includes_readers_of_written_data(c_conn):
    imp = graph.impact(c_conn, one(c_conn, "add"), 5)
    assert imp.writes == ["g_total"]
    assert imp.data_readers == {"get": ["g_total"]}
    got = {(h.fn.qualified_name, h.fn.file): (h.distance, h.confidence) for h in imp.callers.hits}
    assert got[("get", "lib/shared.c")] == (1, "data")
    assert got[("main", "prog_b/main.c")] == (2, "data")  # calls get(), which reads what add() writes
    assert got[("dispatch", "lib/table.c")] == (2, "via_pointer")


def test_impact_explains_its_limits(c_conn):
    imp = graph.impact(c_conn, one(c_conn, "on_a"), 5)
    assert any("used as a function pointer" in n for n in imp.blind_spots)
    assert any("#ifdef" in n for n in imp.blind_spots)


def test_impact_of_a_clashing_function_says_so(c_conn):
    imp = graph.impact(c_conn, one(c_conn, "bump@prog_a/util.c"), 5)
    assert any("same clang ID" in n for n in imp.blind_spots)
    assert [h.fn.qualified_name for h in imp.callers.hits] == ["util_run", "main"]


def test_text_output_is_capped_and_says_what_was_left_out(c_conn):
    w = graph.walk(c_conn, one(c_conn, "add"), "callers", 5)
    text = render.walk_text(w, limit=2)
    assert "... and 3 more" in text
    assert "[via_pointer] = " not in text  # the legend only explains labels that are printed
    full = render.walk_text(w, limit=50)
    assert "[via_pointer] = a call through a function pointer" in full


def test_answers_are_deterministic(repo_factory, tmp_path):
    from conftest import make_repo

    outs = []
    for name in ("x", "y"):
        root = make_repo(tmp_path / name, "c_multi")
        build_index(root, jobs=1)
        conn = store.connect(root)
        outs.append(render.impact_text(graph.impact(conn, symbols.find(conn, "add")[0], 5)))
    assert outs[0] == outs[1]
