"""Data users of fields and globals (build item 5, part B) on fixtures written to trip it up
(tests/fixtures/c_fields)."""

import json
import os

import pytest

from aerotrace.analysis import certainty, data, render_data, symbols
from aerotrace.cli import main
from aerotrace.index import store
from aerotrace.index.build import build_index
from conftest import GOLDEN


@pytest.fixture
def fx(repo_factory):
    root = repo_factory("c_fields")
    build_index(root, jobs=1)
    conn = store.connect(root)
    yield root, conn
    conn.close()


def one(conn, query):
    found = data.find_variables(conn, query) or data.gone_fields(conn, query)
    assert len(found) == 1, [v.shown for v in found]
    return found[0]


def users(conn, query, **kw):
    return data.data_users_of(conn, one(conn, query), **kw)


def names(res):
    return [u.fn.name for u in res.uses]


def fn(conn, name):
    [f] = symbols.find(conn, name)
    return f


# ---------------------------------------------------------------- finding the variable


def test_a_bare_name_lists_every_field_and_global_with_it(fx):
    _, conn = fx
    found = data.find_variables(conn, "count")
    assert sorted(v.shown + "@" + v.file for v in found) == [
        "anon_t::count@include/fields.h",
        "buf::count@include/fields.h",
        "conn::count@include/fields.h",
        "count@a/state.c",
        "count@b/state.c",
    ]


def test_static_globals_with_the_same_name_in_files_with_the_same_name(fx):
    _, conn = fx
    assert names(users(conn, "count@a/state.c")) == ["a_next"]
    assert names(users(conn, "count@b/state.c")) == ["b_peek"]


def test_members_of_unnamed_unions_are_found_by_the_name_people_type(fx):
    _, conn = fx
    v = one(conn, "pair::i")  # the unnamed union's spelling is skipped
    assert v.shown == "pair::val.i"
    assert one(conn, "pair::val.i").id == v.id
    assert names(data.data_users_of(conn, v)) == ["pair_int"]
    assert names(users(conn, "pair::a")) == ["pair_anon"]  # C11 anonymous member: p->a
    assert "(unnamed" not in render_data.data_text(data.data_users_of(conn, v))


def test_global_dot_field_means_the_field_of_its_struct_type(fx):
    _, conn = fx
    assert one(conn, "g_main.fd").qualified_name == "conn::fd"


# ---------------------------------------------------------------- declaration question: every access


def test_every_read_and_write_of_a_field_writers_first(fx):
    _, conn = fx
    r = users(conn, "conn::count")
    got = [(u.fn.name, u.writes, u.reads, u.certainty) for u in r.uses]
    assert got == [
        ("touch", [18], [], "exact"),  # struct passed by pointer
        ("touch_array", [19], [], "exact"),  # field reached through an array element
        ("reset_all", [33], [], "exact"),  # written through a macro: the line of the macro use
        ("bump_any", [35], [], "exact"),  # a cast back to the real type is seen
        ("put", [44], [], "exact"),
        ("leak_count", [], [27, 30], "exact"),
        ("get", [], [45], "exact"),
    ]
    # same field name in other structs, void *, offsetof and a branch that is not compiled: never listed
    for not_here in ("buf_count", "anon_count", "wipe", "count_offset", "other_config"):
        assert not_here not in names(r)
    assert all(u.reason == "names this field" for u in r.uses)


@pytest.mark.xfail(strict=True, reason="#37: designated initializers inside functions record no write")
def test_a_compound_literal_inside_a_function_is_a_write(fx):
    _, conn = fx
    assert "reset_conn" in names(users(conn, "conn::count"))


def test_designated_initializers_at_file_scope(fx):
    _, conn = fx
    r = users(conn, "conn::count")
    assert [(i.variable.name, i.file, i.line, i.certainty) for i in r.initializers] == [
        ("table", "src/fields.c", 8, "exact")
    ]
    a = users(conn, "anon_t::count")
    assert [(i.variable.name, i.line) for i in a.initializers] == [("anon_default", 10)]
    assert names(a) == ["anon_default_count", "anon_count"]  # not mixed with conn::count


def test_globals_of_the_struct_type_may_set_it_by_position(fx):
    _, conn = fx
    r = users(conn, "conn::count")
    # `pos = {5, 6}` sets count by position; the index can't see that (#26), so every global of the type is
    # listed as unknown
    # `wrapped` is a struct wrap, which contains a struct conn: {{1, 2}, 3} sets it by position too
    assert sorted(g.name for g in r.by_position) == [
        "conns",
        "g_main",
        "gone_init",
        "pos",
        "table",
        "wrapped",
    ]
    # a global declared with a typedef name other than the struct's is not matched; the limit says so
    assert users(conn, "node::v").by_position == []
    assert any("node_t for struct node" in n for n in users(conn, "node::v").blind_spots)
    assert "unknown" in render_data.data_text(r)
    assert any("by position" in n and "#26" in n for n in r.blind_spots)


def test_address_of_a_field_passed_on_is_unknown(fx):
    _, conn = fx
    r = users(conn, "conn::count")
    assert [(e.fn.name, e.line, e.detail, e.certainty) for e in r.escapes] == [
        ("leak_count", 27, "passed to fill()", "unknown"),
        ("leak_count", 30, "address returned", "unknown"),
    ]
    fd = users(conn, "conn::fd")
    assert [(e.fn.name, e.line, e.detail) for e in fd.escapes] == [("leak_count", 28, "address stored in p")]


@pytest.mark.xfail(strict=True, reason="#18: an array element passed by value counts as the array escaping")
def test_an_array_element_passed_by_value_is_not_an_escape(fx):
    _, conn = fx
    assert users(conn, "fds").escapes == []


def test_blind_spots_are_stated(fx):
    _, conn = fx
    notes = " ".join(users(conn, "conn::count").blind_spots)
    for words in ("per struct type, not per object", "void *", "offsetof", "#24", "sizeof", "#27", "##"):
        assert words in notes
    assert "union" in " ".join(users(conn, "pair::i").blind_spots)
    assert "union" not in " ".join(users(conn, "conn::count").blind_spots)


def test_fields_have_no_callers_by_default_and_other_names_are_named(fx):
    _, conn = fx
    r = users(conn, "conn::count")
    assert r.depth == 0 and r.upstream == []
    assert sorted(v.shown for v in r.same_name) == [
        "anon_t::count",
        "buf::count",
        "count",
        "count",
    ]


# ---------------------------------------------------------------- a field that is gone


def test_a_removed_field_still_used(fx):
    _, conn = fx
    r = users(conn, "conn::gone")
    assert not r.target.exists
    assert [(b.fn.name, b.line, b.certainty) for b in r.broken] == [
        ("still_uses_gone", 4, "exact"),
        ("bump_twice", 5, "exact"),  # two macro uses on one line: one row
        ("bump_once", 7, "exact"),  # the error is at the macro's use, not its definition
    ]
    # removing a field is exactly when positional initializers shift: still listed
    assert "pos" in [g.name for g in r.by_position]
    notes = " ".join(r.blind_spots)
    # `{.gone = 1}` (src/broken.c:9) fails with "field designator ...", which is not recorded (#25)
    assert "field designator" in notes and "#25" in notes
    assert "count as one place" in notes


def test_compile_errors_naming_another_type_are_probable(fx):
    _, conn = fx
    assert [(b.fn.name, b.certainty) for b in users(conn, "conn::lost").broken] == [("via_typedef", "exact")]
    # asked under another known struct (a typedef, a base): still listed, but only probable
    r = data.data_users_of(conn, data.gone_fields(conn, "buf::lost")[0])
    assert [(b.fn.name, b.record, b.certainty) for b in r.broken] == [("via_typedef", "conn", "probable")]


def test_a_typo_in_the_struct_name_is_not_found(fx, capsys):
    root, conn = fx
    assert data.gone_fields(conn, "Typo::lost") == [] and data.gone_fields(conn, "Typo::count") == []
    assert data.find_variables(conn, "Typo::count") == []
    assert main(["data", "Typo::count", "--root", str(root)]) == 1
    err = capsys.readouterr().err
    assert (
        "No field or global named 'Typo::count'" in err and "Did you mean: " in err and "conn::count" in err
    )


# ---------------------------------------------------------------- value question (--from)


def test_value_question_on_a_field_is_probable(fx):
    _, conn = fx
    r = data.data_users_of(conn, one(conn, "conn::count"), writer_id=fn(conn, "put").id)
    assert [(u.fn.name, u.certainty) for u in r.uses] == [("get", "probable")]
    assert r.uses[0].reason == "reads the same field, maybe of another object"
    assert "put" not in names(r)
    # &c->count is not a read of the value; the places that take the address are unknown, never dropped
    assert [(e.fn.name, e.line, e.certainty) for e in r.escapes] == [
        ("leak_count", 27, "unknown"),
        ("leak_count", 30, "unknown"),
    ]


def test_value_question_keeps_the_places_that_take_the_address(fx):
    _, conn = fx
    # take() does keep = &counter; peek() reads *keep later. take is not a reader, but must be listed.
    r = data.data_users_of(conn, one(conn, "counter"), writer_id=fn(conn, "bump").id)
    assert r.uses == []
    assert [(e.fn.name, e.detail, e.certainty) for e in r.escapes] == [
        ("take", "address stored in keep", "unknown")
    ]
    assert not any("does not write" in n for n in r.blind_spots)
    assert "take  src/fields.c:56: address stored in keep" in render_data.data_text(r)


def test_value_question_on_an_array_global_is_probable(fx):
    _, conn = fx
    r = data.data_users_of(conn, one(conn, "arr"), writer_id=fn(conn, "set_arr").id)
    assert [(u.fn.name, u.certainty, u.reason) for u in r.uses] == [
        ("get_arr", "probable", "reads the same array, maybe another element")
    ]


def test_value_question_on_a_global_is_exact(fx):
    _, conn = fx
    r = data.data_users_of(conn, one(conn, "g_total"), writer_id=fn(conn, "log_num").id)
    assert [(u.fn.name, u.certainty) for u in r.uses] == [("total", "exact")]


def test_value_question_on_a_struct_global_goes_through_its_fields(fx):
    _, conn = fx
    r = data.data_users_of(conn, one(conn, "g_main"), writer_id=fn(conn, "main_fd").id)
    assert [f.shown for f in r.via_fields] == ["conn::fd"]
    assert [(u.fn.name, u.certainty, u.via_field) for u in r.uses] == [("fd_of", "probable", "conn::fd")]
    assert [(e.fn.name, e.line) for e in r.escapes] == [("leak_count", 28)]  # &c->fd: kept, as unknown


def test_a_struct_global_points_to_its_fields(fx):
    _, conn = fx
    r = users(conn, "g_main")
    assert r.struct_fields == [("count", 7, 5), ("fd", 4, 2)] and r.struct_fields_total == 2
    assert "ask for g_main.<field>" in render_data.data_text(r)


def test_value_question_when_the_function_does_not_write_it(fx):
    _, conn = fx
    r = data.data_users_of(conn, one(conn, "g_total"), writer_id=fn(conn, "total").id)
    assert r.uses == []
    assert any("does not write g_total" in n for n in r.blind_spots)


# ---------------------------------------------------------------- globals: callers of the users


def test_a_global_lists_the_callers_of_its_users(fx):
    _, conn = fx
    r = users(conn, "g_total")
    assert [(u.fn.name, u.writes, u.reads) for u in r.uses] == [("log_num", [14], []), ("total", [], [49])]
    assert r.depth == 2
    assert [(h.fn.name, h.distance, h.via) for h in r.upstream] == [
        ("caller_of_log", 1, "log_num"),
        ("caller_of_caller", 2, "caller_of_log"),
    ]
    one_step = users(conn, "g_total", depth=1)
    assert [h.fn.name for h in one_step.upstream] == ["caller_of_log"] and one_step.truncated
    assert any("outside this repo" in n for n in r.blind_spots)  # extern
    assert not any("outside this repo" in n for n in users(conn, "count@a/state.c").blind_spots)  # static


def test_certainty_of_access():
    assert certainty.of_access("exact", "field") == ("exact", "names this field")
    assert certainty.of_access("ambiguous", "global")[0] == "probable"
    assert certainty.of_access("exact", "field", "value")[0] == "probable"
    assert certainty.of_access("exact", "global", "value")[0] == "exact"


# ---------------------------------------------------------------- output


def test_text_summary_first_and_caps(fx):
    _, conn = fx
    r = users(conn, "conn::count")
    out = render_data.data_text(r, limit=2)
    lines = out.splitlines()
    assert lines[0].startswith("Data users of conn::count (include/fields.h:4): field of conn, type int.")
    assert lines[1].startswith("Summary: 7 functions use it in 1 file: 5 write it, 2 only read it (7 exact).")
    assert "  - touch  src/fields.c  first use 18: writes 18" in lines
    assert "  ... and 5 more functions (use --all to see every one, or --limit N)." in lines
    full = render_data.data_text(r, show_all=True)
    assert "  - get  src/fields.c  first use 45: reads 45" in full and "more functions" not in full
    assert render_data.data_text(r) == render_data.data_text(users(conn, "conn::count"))  # deterministic


def test_text_answer_matches_golden(fx):
    _, conn = fx
    out = render_data.data_text(users(conn, "conn::count")) + "\n"
    golden = GOLDEN / "data_c_fields.txt"
    if os.environ.get("AEROTRACE_UPDATE_GOLDEN"):
        golden.write_text(out)
    assert out == golden.read_text()


def test_cli_data(fx, capsys):
    root, _ = fx
    assert main(["data", "conn::count", "--root", str(root), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["uses_total"] == 7 and out["summary"]["writers"] == 5
    assert out["target"]["id"] == "c:@S@conn@FI@count" and out["may_set_by_position_omitted"] == 0
    assert out["uses"][0]["function"]["name"] == "touch" and out["uses"][0]["certainty"] == "exact"
    assert [e["detail"] for e in out["escapes"]] == ["passed to fill()", "address returned"]
    assert main(["data", "count", "--root", str(root)]) == 1
    assert "matches 5 variables" in capsys.readouterr().err
    assert main(["data", "conn::count", "--from", "put", "--root", str(root)]) == 0
    assert "Who reads what put" in capsys.readouterr().out
    assert main(["data", "conn::nothing", "--root", str(root)]) == 1
    assert "No field or global named" in capsys.readouterr().err
