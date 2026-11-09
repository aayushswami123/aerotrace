"""Test impact (static links): finding test entry points and linking them to changed code.

The fixture `testimpact` has one of each kind (GoogleTest TEST and TEST_F with a fixture SetUp, Unity,
CMocka with a setup, a test program with main) and traps: a Unity test that is never registered, one
registered inside `#if 0`, a `TEST` inside `#if 0`, a `test_helper()` outside the tests, a test file that
is not indexed, a call through a function pointer, and a function whose only test is two calls away.
"""

import json
import re

import pytest

from aerotrace.analysis import diff, render, symbols, testimpact
from aerotrace.cli import main
from aerotrace.index import store
from aerotrace.index.build import build_index
from aerotrace.index.history import Outliner
from conftest import GOLDEN
from test_diff import git


@pytest.fixture
def root(repo_factory):
    r = repo_factory("testimpact")
    build_index(r)
    return r


def _one(conn, name):
    [fn] = symbols.find(conn, name)
    return fn


def _ask(root, name, depth=8):
    conn = store.connect(root)
    try:
        return testimpact.for_function(conn, root, _one(conn, name), depth)
    finally:
        conn.close()


def _tests(res):
    return {t.test: t.level for t in res.tests}


# ---------------------------------------------------------------- entry points


def test_finds_every_kind_of_entry_point_and_nothing_else(root):
    conn = store.connect(root)
    entries = testimpact.find_entries(conn, root)
    names = symbols.get_many(conn, list(entries))
    got = sorted(
        (e.kind, e.test, e.role, names[e.fn_id].qualified_name) for es in entries.values() for e in es
    )
    assert got == [
        ("cmocka", "tests/cmocka_calc.c:mul_works", "test", "mul_works"),
        ("cmocka", "tests/cmocka_calc.c:name_works", "setup", "fast_mode"),
        ("cmocka", "tests/cmocka_calc.c:name_works", "test", "name_works"),
        ("gtest", "Calc.Adds", "test", "Calc_Adds_Test::TestBody"),
        ("gtest", "CalcFixture.AppliesSub", "setup", "CalcFixture::SetUp"),
        ("gtest", "CalcFixture.AppliesSub", "test", "CalcFixture_AppliesSub_Test::TestBody"),
        ("gtest", "CalcFixture.KnowsMode", "setup", "CalcFixture::SetUp"),
        ("gtest", "CalcFixture.KnowsMode", "test", "CalcFixture_KnowsMode_Test::TestBody"),
        ("main", "tests/cmocka_calc.c", "program", "main"),
        ("main", "tests/test_scale.c", "program", "main"),
        ("main", "tests/test_unity.c", "program", "main"),
        ("unity", "tests/test_unity.c:test_add", "test", "test_add"),
    ]
    # Never registered, registered only in a comment or #if 0, or not in a test file: not tests.
    flat = " ".join(t for *_, t in got)
    for not_a_test in ("test_unused", "test_sub_disabled", "test_helper", "DividesDisabled", "Divides"):
        assert not_a_test not in flat
    assert testimpact.unindexed_test_files(conn, root) == ["tests/extra_test.cc"]


def test_is_test_file():
    yes = [
        "tests/a.c",
        "test/x/y.cpp",
        "src/test_queue.c",
        "src/queue_test.cc",
        "lib/foo_tests.cpp",
        "a/fooUnitTest.cc",
    ]
    no = ["src/queue.c", "src/memtest.c", "src/contest.c", "src/latest.c", "src/test.h", "src/latest.c.bak"]
    assert all(testimpact.is_test_file(p) for p in yes)
    assert not any(testimpact.is_test_file(p) for p in no)


# ---------------------------------------------------------------- linking


def test_direct_callers_are_exact(root):
    res = _ask(root, "add")
    assert _tests(res) == {
        "Calc.Adds": "exact",
        "tests/test_unity.c:test_add": "exact",
        "tests/test_scale.c": "exact",  # main -> scale -> twice -> add
    }
    assert res.not_known == [] and res.no_test_reaches == []


def test_a_call_through_a_pointer_is_probable_and_a_helper_is_not_a_test(root):
    res = _ask(root, "sub")
    assert _tests(res) == {"CalcFixture.AppliesSub": "probable"}
    [t] = res.tests
    assert "function pointer" in t.reason and "apply -> sub" in t.reason


def test_fixture_setup_and_cmocka_setup_count_and_data_links_are_probable(root):
    res = _ask(root, "set_mode")
    assert _tests(res) == {
        "CalcFixture.AppliesSub": "exact",
        "CalcFixture.KnowsMode": "exact",
        "tests/cmocka_calc.c:name_works": "exact",
        "tests/test_scale.c": "probable",  # main -> mode_name, which reads calc_mode that set_mode writes
    }
    assert all(t.reason.startswith("its setup calls it") for t in res.tests if t.kind != "main")


def test_code_no_test_calls_is_not_known_never_untested(root):
    res = _ask(root, "divide")
    assert res.tests == [] and res.no_test_reaches == []
    assert [(n.what, n.why) for n in res.not_known] == [("divide", "no C/C++ test calls it")]
    text = render.tests_text(res)
    assert "No tests to list. This does not mean no test runs this code" in text
    assert "tests/extra_test.cc" in text  # the unindexed test file that does call divide


def test_depth_limit_is_said_and_never_hides_a_test_silently(root):
    res = _ask(root, "twice", depth=1)
    assert res.tests == [] and res.truncated
    [n] = res.not_known
    assert "within 1 call steps" in n.why
    assert any("Stopped at depth 1" in x for x in res.limits)
    assert _tests(_ask(root, "twice")) == {"tests/test_scale.c": "exact"}


def test_editing_a_test_lists_that_test(root):
    res = _ask(root, "Calc_Adds_Test::TestBody")
    assert [(t.test, t.level, t.reason) for t in res.tests] == [
        ("Calc.Adds", "exact", "this test itself changed")
    ]


# ---------------------------------------------------------------- whole changes


def _patch_res(root, old, new, file="src/calc.c"):
    before = (root / file).read_text()
    assert before.count(old) == 1
    after = before.replace(old, new)
    import difflib

    text = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True), after.splitlines(keepends=True), f"a/{file}", f"b/{file}"
        )
    )
    conn = store.connect(root)
    d = diff.analyse(conn, root, diff.load_patch(root, text), mode="patch", now=Outliner(root).now)
    return testimpact.for_diff(conn, root, d)


def test_changed_global_counts_tests_of_its_readers_and_writers(root):
    res = _patch_res(root, "int calc_mode = 0;", "int calc_mode = 1;")
    assert res.changed_decls == 1
    got = _tests(res)
    assert set(got) == {
        "CalcFixture.AppliesSub",
        "CalcFixture.KnowsMode",
        "tests/cmocka_calc.c:name_works",
        "tests/test_scale.c",
    }
    assert set(got.values()) == {"probable"}  # linked through a changed declaration, not a call


def test_a_macro_change_is_not_known_not_dropped(root):
    res = _patch_res(root, "#define CALC_LIMIT 100", "#define CALC_LIMIT 200", file="include/calc.h")
    assert res.tests == []
    [n] = res.not_known
    assert n.what == "include/calc.h" and "outside any function" in n.why
    assert "No tests to list. This does not mean" in render.tests_text(res)


def test_git_mode_through_the_cli(root, capsys):
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "--no-verify", "-m", "init")
    p = root / "src" / "calc.c"
    p.write_text(p.read_text().replace("return a * b;", "return b * a;"))
    assert main(["tests", "--root", str(root), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["coverage"] == {"state": "not_recorded"}
    assert out["changed_functions"] == 1
    assert {t["id"]: t["level"] for t in out["tests"]} == {
        "tests/cmocka_calc.c:mul_works": "exact",
        "tests/test_scale.c": "exact",
    }
    assert main(["tests", "--root", str(root), "--base", "HEAD"]) == 0
    text = capsys.readouterr().out
    assert "Run these tests (2, most certain first):" in text
    assert "the CMocka program built from tests/cmocka_calc.c" in text


def test_cli_symbol_and_bad_symbol(root, capsys):
    assert main(["tests", "--root", str(root), "--symbol", "add"]) == 0
    assert "--gtest_filter=Calc.Adds" in capsys.readouterr().out
    assert main(["tests", "--root", str(root), "--symbol", "no_such_fn"]) == 1


def test_golden_text(root):
    got = render.tests_text(_ask(root, "set_mode")) + "\n----\n" + render.tests_text(_ask(root, "divide"))
    assert got + "\n" == (GOLDEN / "tests_testimpact.txt").read_text()


# ---------------------------------------------------------------- wording rules


BANNED = [re.compile(r"no tests? (are |is )?affected", re.I), re.compile(r"\b0 tests\b")]


@pytest.mark.parametrize("name", ["add", "sub", "mul", "divide", "set_mode", "twice", "mode_name"])
def test_never_claims_no_tests_when_it_does_not_know(root, name):
    for depth in (1, 8):
        res = _ask(root, name, depth)
        text = render.tests_text(res)
        for b in BANNED:
            assert not b.search(text), text
        if "No tests to list" in text:
            assert "This does not mean no test runs this code" in text
        assert res.no_test_reaches == []  # static links alone never prove a function untested
        assert json.dumps(render.tests_json(res)) == json.dumps(render.tests_json(_ask(root, name, depth)))
