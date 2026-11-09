"""Signature impact (build item 5, part A) on fixtures written to trip it up (tests/fixtures/c_data)."""

import json

import pytest

from aerotrace.analysis import render, signature, symbols
from aerotrace.cli import main
from aerotrace.index import store
from aerotrace.index.build import build_index
from aerotrace.toolchain.compdb import find_units, system_include_args


@pytest.fixture
def data(repo_factory):
    root = repo_factory("c_data")
    build_index(root, jobs=1)
    conn = store.connect(root)
    yield root, conn
    conn.close()


def one(conn, query):
    [f] = symbols.find(conn, query)
    return f


def test_every_way_a_return_value_is_used(data):
    _, conn = data
    r = signature.signature_impact(conn, one(conn, "parse_num"))
    got = [(s.caller.name, s.line, s.use, s.detail) for s in r.sites if s.caller.name == "uses"]
    assert got == [
        ("uses", 6, "ignored", ""),
        ("uses", 7, "discarded", "cast to (void)"),
        ("uses", 8, "stored", "in ok"),
        ("uses", 9, "stored", "in field count"),
        ("uses", 10, "compared", "with =="),
        ("uses", 11, "checked", ""),
        ("uses", 12, "checked", "negated with !"),
        ("uses", 13, "passed", "to log_num() as argument 1"),
        ("uses", 14, "stored", "in ok (+=)"),
        ("uses", 15, "checked", "negated with !"),  # inside the CHECK() macro
        ("uses", 16, "returned", ""),
    ]
    assert all(s.n_args == 2 and s.certainty == "exact" for s in r.sites if s.caller.name == "uses")


def test_a_call_that_no_longer_compiles_is_still_listed(data):
    # after a parameter is added, every old call has too few arguments: those are the calls that matter
    _, conn = data
    r = signature.signature_impact(conn, one(conn, "parse_num"))
    [broken] = [s for s in r.sites if s.broken]
    assert (broken.caller.name, broken.line, broken.n_args) == ("too_few_args", 8, 1)
    assert "DOES NOT COMPILE NOW: passes 1 argument, it takes 2" in render.signature_text(r)


def test_places_that_store_the_address_and_pointer_calls(data):
    _, conn = data
    r = signature.signature_impact(conn, one(conn, "parse_num"))
    assert [(a.file, a.line, a.in_variable or a.in_function.name, a.how) for a in r.address_sites] == [
        ("src/parse.c", 6, "parsers", "stored in an initializer"),
        ("src/use.c", 25, "setup", "passed to register_parser() as argument 1"),
    ]
    [p] = r.pointer_sites
    assert (p.caller.name, p.line, p.certainty, p.link) == ("through_pointer", 21, "probable", "via_pointer")


def test_void_function_has_no_return_value_uses(data):
    _, conn = data
    r = signature.signature_impact(conn, one(conn, "log_num"))
    assert r.returns_void and r.params == 1
    text = render.signature_text(r)
    assert "It returns void" in text and "How callers use the return value" not in text


def test_same_named_statics_in_two_files_stay_apart(repo_factory):
    root = repo_factory("c_multi")  # prog_a/util.c and prog_b/util.c each have a static bump()
    build_index(root, jobs=1)
    conn = store.connect(root)
    a = signature.signature_impact(conn, one(conn, "bump@prog_a/util.c"))
    b = signature.signature_impact(conn, one(conn, "bump@prog_b/util.c"))
    assert [(s.caller.name, s.caller.file) for s in a.sites] == [("util_run", "prog_a/util.c")]
    assert [(s.caller.name, s.caller.file) for s in b.sites] == [("util_run_b", "prog_b/util.c")]
    conn.close()


def test_virtual_calls_reach_every_override(repo_factory):
    root = repo_factory("cpp_virtual")
    build_index(root, jobs=1)
    conn = store.connect(root)
    r = signature.signature_impact(conn, one(conn, "Circle::area"))
    assert [(s.caller.name, s.line, s.link, s.certainty) for s in r.sites] == [
        ("total", 8, "virtual", "probable")
    ]
    assert r.params == 0 and not r.returns_void
    conn.close()


def test_callers_reached_through_a_pointer_are_never_exact(repo_factory):
    # on_a is only reached through the handler table: dispatch() calls it through a pointer, and main@prog_b
    # calls dispatch(). Both must stay probable (via_pointer), never exact.
    root = repo_factory("c_multi")
    build_index(root, jobs=1)
    conn = store.connect(root)
    r = signature.signature_impact(conn, one(conn, "on_a"), depth=1)
    assert [(s.caller.name, s.certainty) for s in r.pointer_sites] == [("dispatch", "probable")]
    assert [(h.fn.name, h.fn.file, h.confidence, h.distance) for h in r.upstream] == [
        ("main", "prog_b/main.c", "via_pointer", 2)
    ]
    conn.close()


def test_upstream_callers_and_depth(repo_factory):
    root = repo_factory("c_multi")
    build_index(root, jobs=1)
    conn = store.connect(root)
    r = signature.signature_impact(conn, one(conn, "add"), depth=2)
    assert {s.caller.name for s in r.sites} == {"main", "on_a", "on_b"}
    # on_a and on_b are in a handler table called by dispatch(), which main@prog_b calls
    assert {(h.fn.name, h.confidence) for h in r.upstream} >= {("dispatch", "via_pointer")}
    conn.close()


# ---------------------------------------------------------------- the other new facts in the index


def test_escapes_initializers_and_missing_members_are_recorded(data):
    _, conn = data
    escapes = [
        (r[0], r[1], r[2])
        for r in conn.execute(
            """SELECT f.name, e.line, e.detail FROM escapes e JOIN functions f ON f.id = e.function_id
               ORDER BY e.line"""
        )
    ]
    # &g_total stored in a pointer and passed to fill() escape; memset(&g_total, ...) is a write, not an escape
    assert escapes == [("escapes", 41, "address stored in p"), ("escapes", 42, "passed to fill()")]
    inits = conn.execute(
        """SELECT g.qualified_name, f.qualified_name, i.line FROM initializers i
           JOIN variables g ON g.id = i.variable_id JOIN variables f ON f.id = i.field_id"""
    ).fetchall()
    assert [tuple(r) for r in inits] == [("defaults", "conn::fd", 7)]
    assert [tuple(r) for r in conn.execute("SELECT * FROM missing_members")] == [
        ("src/broken.c", 4, "gone", "conn")
    ]


# ---------------------------------------------------------------- CLI and JSON


def test_cli_signature_json(data, capsys):
    root, _ = data
    assert main(["signature", "parse_num", "--root", str(root), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["parameters"] == 2 and out["call_sites_total"] == 12
    assert out["return_value_uses"]["checked"] == 3
    assert [s["compiles"] for s in out["call_sites"]].count(False) == 1


def test_operator_spelling_does_not_read_tokens(monkeypatch, data):
    # The first draft read every token of each comparison to name its operator; on SQLite's big macro
    # expansions that made indexing 30x slower. The operator must come from libclang's operator kind.
    from aerotrace.frontends.cpp import parse

    if not parse._has_operator_api():
        pytest.skip("this libclang has no operator-kind API")
    calls = []
    monkeypatch.setattr(parse.Collector, "binary_op", staticmethod(lambda c: calls.append(c) or None))
    root, _ = data
    unit = next(u for u in find_units(root, None, []).units if u.file.endswith("use.c"))
    res = parse.parse_unit(unit, root, [], system_include_args("c"))
    assert any(s[7] == "compared" and s[8] == "with ==" for s in res["call_sites"])
    assert calls == []
