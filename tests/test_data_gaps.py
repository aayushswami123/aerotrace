"""Data facts the parser used to miss (#18, #19, #24, #25, #26, #27, #29, #37, #52), on a fixture written to trip
them up (tests/fixtures/c_gaps)."""

import pytest

from aerotrace.analysis import data
from aerotrace.index import store
from aerotrace.index.build import build_index


@pytest.fixture(scope="module")
def conn(tmp_path_factory):
    from conftest import make_repo

    root = make_repo(tmp_path_factory.mktemp("gaps"), "c_gaps")
    build_index(root, jobs=1)
    c = store.connect(root)
    yield c
    c.close()


def users(conn, query):
    found = data.find_variables(conn, query) or data.gone_fields(conn, query)
    assert len(found) == 1, [v.shown for v in found]
    return data.data_users_of(conn, found[0])


def uses(conn, query):
    return [(u.fn.name, u.reads, u.writes) for u in users(conn, query).uses]


def inits(conn, query):
    return [i.variable.name for i in users(conn, query).initializers]


# ---------------------------------------------------------------- #52: x++ and x += n read and write


def test_increments_and_compound_assignments_read_and_write(conn):
    assert uses(conn, "level") == [
        ("bump_level", [4], [4]),  # level++
        ("pre_bump_level", [5], [5]),  # ++level
        ("add_level", [6], [6]),  # level += n
        ("set_level", [], [8]),  # level = 1 does not read it
    ]
    assert uses(conn, "server::port") == [("or_port", [7], [7])]  # a field: server.port |= 1


# ---------------------------------------------------------------- #37 and #26 inside functions


def test_initializers_inside_functions_write_the_fields(conn):
    got = dict((n, (r, w)) for n, r, w in uses(conn, "inner::y"))
    assert got["local_designated"] == ([], [11])  # struct inner i = {.y = 1}
    assert got["static_designated"] == ([], [12])  # static struct inner s = {.y = 2}
    assert got["local_positional"] == ([], [13])  # {1, 2}
    assert got["literal_positional"] == ([], [15])  # (struct inner){3, 4}
    x = dict((n, (r, w)) for n, r, w in uses(conn, "inner::x"))
    assert x["local_zero"] == ([14], [])  # {0} is not counted as setting x; z.x is read
    assert x["local_designated"] == ([11], [])  # {.y = 1} does not name x


# ---------------------------------------------------------------- #24: offsetof


def test_offsetof_is_a_use_of_the_field(conn):
    assert ("y_offset", [18], []) in uses(conn, "inner::y")
    assert "y_offset" not in [n for n, _, _ in uses(conn, "inner::x")]


# ---------------------------------------------------------------- #26: by position at file scope


def test_values_in_order_set_fields_with_left_out_braces(conn):
    # full = {10, 1, 2, 3, 4, 5, "srv", 6}: hz, (the unnamed bit-field takes no value), in.x, in.y, the union's
    # first member, the anonymous struct's two fields, name (a string) and last
    for field in (
        "cfg::hz",
        "inner::x",
        "inner::y",
        "cfg::u.as_int",
        "cfg::anon_a",
        "cfg::anon_b",
        "cfg::name",
    ):
        assert "full" in inits(conn, field), field
    assert "full" in inits(conn, "cfg::last")
    assert inits(conn, "cfg::u.as_float") == []  # {x} only sets a union's first member


def test_values_in_order_with_braces(conn):
    # braced = {10, {1, 2}, {3}, {4, 5}, "srv", 6}
    for field in ("cfg::hz", "cfg::in", "inner::y", "cfg::u.as_int", "cfg::anon_b", "cfg::last"):
        assert "braced" in inits(conn, field), field
    # tables[] = {{1, 2, 3}, 4, 5}: the second element's braces are left out: hz = 4, in.x = 5
    assert "tables" in inits(conn, "inner::y") and "tables" in inits(conn, "cfg::hz")
    # copy_src = {1, 2} sets both fields of an inner
    assert "copy_src" in inits(conn, "inner::x") and "copy_src" in inits(conn, "inner::y")


def test_values_that_cannot_be_matched_are_left_out_and_stay_unknown(conn):
    # after_designator = {.hz = 1, 2, 3}: hz by name; 2 and 3 are not followed
    assert "after_designator" in inits(conn, "cfg::hz")
    assert "after_designator" not in inits(conn, "cfg::in")
    assert "after_designator" not in inits(conn, "inner::x")
    # ... so the global is still listed as one that may set the field by position (unknown)
    r = users(conn, "cfg::in")
    assert "after_designator" in [g.name for g in r.by_position]
    # copied = {10, {0}, .last = 7}: in.x = 0 by position inside the braces, last by name
    assert "copied" in inits(conn, "inner::x") and "copied" in inits(conn, "cfg::last")
    assert "copied" not in inits(conn, "cfg::u.as_int")
    # zero = {0} sets everything to zero: not counted
    assert "zero" not in inits(conn, "cfg::hz")


# ---------------------------------------------------------------- #27: &x in a file-scope table


def test_addresses_stored_in_file_scope_initializers_are_escapes(conn):
    r = users(conn, "server::config_hz")
    got = [(e.fn.name, e.fn.file, e.line, e.detail, e.certainty) for e in r.escapes]
    assert got == [("configs", "src/tables.c", 11, "address stored in the initializer of configs", "unknown")]
    assert r.escapes[0].in_variable is not None and r.escapes[0].in_variable.name == "configs"
    assert [(e.fn.name, e.line) for e in users(conn, "level").escapes] == [("configs", 12)]
    # &server.pipe[1] and the array itself (as a pointer) leave too
    pipe = [(e.fn.name, e.line) for e in users(conn, "server::pipe").escapes]
    assert ("pipe_end", 14) in pipe and ("as_ptr", 15) in pipe
    # a plain value and a null pointer are not addresses of anything
    assert users(conn, "plain_value").escapes == []


# ---------------------------------------------------------------- #18: array elements by value


def test_an_array_element_by_value_is_not_an_escape(conn):
    pipe = [(e.fn.name, e.detail) for e in users(conn, "server::pipe").escapes]
    assert "pass_element" not in [n for n, _ in pipe]  # use_int(server.pipe[0])
    assert ("pass_array", "passed to fill_pipe()") in pipe  # the whole array
    assert ("element_address", "address returned") in pipe  # &server.pipe[0]
    # one row of a 2D array is still an array: passing it passes an address
    assert [(e.fn.name, e.detail) for e in users(conn, "server::names").escapes] == [
        ("pass_row", "passed to use_ptr()")
    ]


# ---------------------------------------------------------------- #19: no machine paths


def test_unnamed_records_hold_no_machine_path(conn):
    rows = conn.execute("SELECT qualified_name, container, type FROM variables ORDER BY id").fetchall()
    text = " ".join(str(x) for r in rows for x in r)
    assert "(unnamed" in text or "(anonymous" in text
    assert "/" not in text  # only the file name is kept
    assert users(conn, "cfg::as_int").target.shown == "cfg::u.as_int"  # still found by the name people type
    assert users(conn, "cfg::anon_a").target.name == "anon_a"


# ---------------------------------------------------------------- #25 and #29: removed fields and globals


def test_a_removed_global_lists_the_places_that_no_longer_compile(conn):
    r = users(conn, "gone_global")
    assert not r.target.exists and r.target.kind == "global"
    assert [(b.fn.name, b.line, b.certainty) for b in r.broken] == [("read_gone_global", 4, "exact")]
    assert any("extern left in a header" in n for n in r.blind_spots)


def test_more_errors_than_clangs_limit_are_all_recorded(conn):
    r = users(conn, "inner::gone")
    assert len(r.broken) == 30 and {b.fn.name for b in r.broken} == {"use_gone"}


def test_cpp_member_used_without_this_is_probable(conn):
    r = users(conn, "Counter::gone_member")
    assert [(b.fn.name, b.line, b.certainty) for b in r.broken] == [("read_gone", 3, "probable")]
