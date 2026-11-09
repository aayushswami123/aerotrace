"""Writes that go through an address: atomics, __sync builtins and memset-style calls (tests/fixtures/c_atomics)."""

import pytest

from aerotrace.analysis import graph, symbols
from aerotrace.index import store
from aerotrace.index.build import build_index


@pytest.fixture
def conn(repo_factory):
    root = repo_factory("c_atomics")
    build_index(root, jobs=1)
    c = store.connect(root)
    yield c
    c.close()


def fn(conn, name):
    [f] = symbols.find(conn, name)
    return f


def writes(conn, name):
    return graph.data_coupling(conn, fn(conn, name).id)[0]


def reads(conn, name):
    rows = conn.execute(
        """SELECT DISTINCT v.qualified_name FROM edges e JOIN variables v ON v.id = e.dst
           WHERE e.src = ? AND e.kind = 'reads' ORDER BY 1""",
        (fn(conn, name).id,),
    )
    return [r[0] for r in rows]


@pytest.mark.parametrize(
    "name, written",
    [
        ("on_request", ["stats::requests"]),  # atomic_fetch_add_explicit(&st.requests, ...)
        (
            "on_bytes",
            ["stats::bytes", "total"],
        ),  # __atomic_add_fetch(&total), __sync_add_and_fetch(&st.bytes)
        ("set_requests", ["stats::requests"]),  # atomic_store_explicit
        ("reset", ["st"]),  # memset(&st, ...): the whole global
        ("clear_slots", ["stats::slots"]),  # memset(st.slots, ...): an array, no '&'
        ("rename_stats", ["stats::name"]),  # strncpy(st.name, ...)
    ],
)
def test_writes_through_an_address_are_recorded(conn, name, written):
    assert writes(conn, name) == written


def test_atomic_load_is_a_read_not_a_write(conn):
    assert writes(conn, "read_requests") == []
    assert reads(conn, "read_requests") == ["st", "stats::requests"]


def test_taking_an_address_alone_is_not_a_write(conn):
    assert writes(conn, "where_total") == []
    assert reads(conn, "where_total") == ["total"]


def test_impact_follows_atomic_writes_to_their_readers(conn):
    # the point of the fix: a function that only bumps a counter atomically affects whoever reads it
    imp = graph.impact(conn, fn(conn, "on_request"))
    assert ("read_requests", "data") in {(h.fn.qualified_name, h.confidence) for h in imp.callers.hits}


def test_blind_spot_a_write_inside_another_function_is_not_seen(conn):
    # blind spot: take(&total) writes total inside take(); hand_off itself does not show it, and every
    # answer says so in its limits
    assert writes(conn, "hand_off") == []
    assert any("foo(&x)" in n for n in graph.index_limits(conn))
