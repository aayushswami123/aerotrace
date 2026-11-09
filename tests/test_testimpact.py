"""Test impact (static links): finding test entry points and linking them to changed code.

The fixture `testimpact` has one of each kind (GoogleTest TEST, TEST_F with a fixture SetUp, TEST_P,
TYPED_TEST; Unity; CMocka with a setup and a group setup; test programs with main) and traps:
- never registered, or registered only in a comment or `#if 0`; a `TEST` inside `#if 0`;
- `test_helper()` outside the tests; a test file that is not indexed;
- a call through a function pointer; a function whose only test is two calls away;
- layouts from the review of #34: TEST split over two lines, TEST inside a project macro, RUN_TEST split
  over lines and inside a wrapper macro, a generated runner's run_test(), a runner table, a split
  cmocka_unit_test(, and two static helper() with the same name in different files.
"""

import fnmatch
import json
import re
import shutil

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
    names = symbols.get_many(conn, list(entries.by_fn))
    got = sorted(
        (e.kind, e.test, e.role, names[e.fn_id].qualified_name, e.guessed)
        for es in entries.by_fn.values()
        for e in es
    )
    assert got == [
        ("cmocka", "tests/cmocka_calc.c:mul_works", "test", "mul_works", False),
        ("cmocka", "tests/cmocka_calc.c:name_works", "setup", "fast_mode", False),
        ("cmocka", "tests/cmocka_calc.c:name_works", "test", "name_works", False),
        ("cmocka", "tests/cmocka_more.c:split_works", "setup", "group_setup", False),  # group setup
        ("cmocka", "tests/cmocka_more.c:split_works", "test", "split_works", False),  # split macro
        ("cmocka", "tests/cmocka_other.c:other_works", "test", "other_works", False),
        ("gtest", "Calc.Adds", "test", "Calc_Adds_Test::TestBody", False),
        ("gtest", "CalcFixture.AppliesSub", "setup", "CalcFixture::SetUp", False),
        ("gtest", "CalcFixture.AppliesSub", "test", "CalcFixture_AppliesSub_Test::TestBody", False),
        ("gtest", "CalcFixture.KnowsMode", "setup", "CalcFixture::SetUp", False),
        ("gtest", "CalcFixture.KnowsMode", "test", "CalcFixture_KnowsMode_Test::TestBody", False),
        ("gtest", "CalcMacro.ViaMacro", "test", "CalcMacro_ViaMacro_Test::TestBody", False),  # project macro
        ("gtest", "Param.Squares", "test", "Param_Squares_Test::TestBody", False),
        ("gtest", "Typed.Doubles", "test", "Typed_Doubles_Test::TestBody", False),
        ("gtest", "Wrapped.LongName", "test", "Wrapped_LongName_Test::TestBody", False),  # split TEST(
        ("main", "tests/cmocka_calc.c", "program", "main", False),
        ("main", "tests/cmocka_more.c", "program", "main", False),
        ("main", "tests/cmocka_other.c", "program", "main", False),
        ("main", "tests/test_layouts.c", "program", "main", False),
        ("main", "tests/test_scale.c", "program", "main", False),
        ("main", "tests/test_unity.c", "program", "main", False),
        ("unity", "tests/test_layouts.c:test_generated", "test", "test_generated", False),  # generated runner
        ("unity", "tests/test_layouts.c:test_split", "test", "test_split", False),  # RUN_TEST( split
        ("unity", "tests/test_layouts.c:test_table", "test", "test_table", True),  # a runner table: guessed
        ("unity", "tests/test_layouts.c:test_wrapped", "test", "test_wrapped", False),  # wrapper macro
        ("unity", "tests/test_unity.c:test_add", "test", "test_add", False),
    ]
    # Never registered, registered only in a comment or #if 0, or not in a test file: not tests.
    flat = " ".join(str(t) for t in got)
    for not_a_test in ("test_unused", "test_sub_disabled", "test_helper", "DividesDisabled", "Divides"):
        assert not_a_test not in flat
    # Passed as a pointer in a test file, but not a test: named in the limits, not listed.
    assert entries.unrecognised == ["sub (tests/calc_test.cpp:11)", "sub (tests/test_layouts.c:30)"]
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


# ---------------------------------------------------------------- GoogleTest filters

# GoogleTest's own naming (docs: advanced.md, "Value-Parameterized Tests" and "Typed Tests"), as a real
# build lists them with --gtest_list_tests (checked in the review of #34).
GTEST_NAMES = {
    "TEST": ["Suite.Name"],
    "TEST_F": ["Suite.Name"],
    "TEST_P": ["All/Suite.Name/0", "All/Suite.Name/1", "Suite.Name/0"],  # last: empty instantiation prefix
    "TYPED_TEST": ["Suite/0.Name", "Suite/1.Name"],
    "TYPED_TEST_P": ["Prefix/Suite/0.Name", "Prefix/Suite/1.Name"],
}
OTHERS = ["Suite.Other", "Other.Name", "Suite.NameLonger", "Suite/0.Other", "All/Suite.Other/0"]


def _gtest_match(pattern: str, name: str) -> bool:
    """GoogleTest's --gtest_filter: ':'-separated globs ('*', '?'), each against the full name."""
    return any(fnmatch.fnmatchcase(name, p) for p in pattern.split(":"))


@pytest.mark.parametrize("macro", sorted(GTEST_NAMES))
def test_gtest_filter_selects_the_test_for_every_macro(macro):
    pat = testimpact.gtest_filter(macro, "Suite", "Name")
    for name in GTEST_NAMES[macro]:
        assert _gtest_match(pat, name), f"{pat} would run 0 tests for {name}"
    if macro in ("TEST", "TEST_F"):
        assert not any(_gtest_match(pat, o) for o in OTHERS)


def test_gtest_filter_of_an_unknown_macro_matches_every_style():
    pat = testimpact.gtest_filter("", "Suite", "Name")
    for names in GTEST_NAMES.values():
        assert all(_gtest_match(pat, n) for n in names)


def test_fixture_filters_and_the_printed_command(root):
    conn = store.connect(root)
    entries = testimpact.find_entries(conn, root)
    filters = {e.test: e.gtest_filter for es in entries.by_fn.values() for e in es if e.kind == "gtest"}
    assert filters["Param.Squares"] == "*Param.Squares/*"
    assert filters["Typed.Doubles"] == "*Typed/*.Doubles"
    assert filters["CalcMacro.ViaMacro"] == "*CalcMacro*.ViaMacro*"
    assert _gtest_match(filters["Typed.Doubles"], "Typed/0.Doubles")
    text = render.tests_text(_ask(root, "mul"))
    assert "--gtest_filter='*CalcMacro*.ViaMacro*:*Param.Squares/*:*Typed/*.Doubles:Wrapped.LongName'" in text
    assert "did not match any test', the filter is wrong" in text


# ---------------------------------------------------------------- linking


def test_direct_callers_are_exact(root):
    res = _ask(root, "add")
    assert _tests(res) == {
        "Calc.Adds": "exact",
        "tests/cmocka_more.c:split_works": "exact",  # its group setup calls add
        "tests/test_layouts.c:test_generated": "exact",
        "tests/test_layouts.c:test_split": "exact",
        "tests/test_layouts.c:test_wrapped": "exact",
        "tests/test_scale.c": "exact",  # main -> scale -> twice -> add
        "tests/test_unity.c:test_add": "exact",
        "tests/test_layouts.c": "probable",  # main calls the tests through a pointer
        "tests/test_layouts.c:test_table": "probable",  # registration guessed
    }
    assert res.not_known == [] and res.no_test_reaches == []


def test_a_call_through_a_pointer_is_probable_and_a_helper_is_not_a_test(root):
    res = _ask(root, "sub")
    assert _tests(res) == {
        "CalcFixture.AppliesSub": "probable",
        "tests/cmocka_other.c:other_works": "exact",
        "tests/test_layouts.c": "probable",
    }
    by = {t.test: t.reason for t in res.tests}
    assert by["CalcFixture.AppliesSub"].endswith("(apply calls sub through a function pointer)")


def test_reason_paths_never_mix_up_same_name_helpers(root):
    by = {t.test: t.reason for t in _ask(root, "set_mode").tests}
    assert by["tests/cmocka_more.c:split_works"] == "calls it: split_works -> helper -> set_mode"
    by = {t.test: t.reason for t in _ask(root, "sub").tests}
    assert by["tests/cmocka_other.c:other_works"] == "calls it: other_works -> helper -> sub"


def test_fixture_setup_and_cmocka_setup_count_and_data_links_are_probable(root):
    res = _ask(root, "set_mode")
    assert _tests(res) == {
        "CalcFixture.AppliesSub": "exact",
        "CalcFixture.KnowsMode": "exact",
        "tests/cmocka_calc.c:name_works": "exact",
        "tests/cmocka_more.c:split_works": "exact",
        "tests/test_scale.c": "probable",  # main -> mode_name, which reads calc_mode that set_mode writes
    }
    by = {t.test: t.reason for t in res.tests}
    assert by["CalcFixture.KnowsMode"].startswith("its setup calls it")
    assert (
        by["tests/test_scale.c"]
        == "main -> mode_name -> set_mode (mode_name reads data that set_mode writes)"
    )


def test_code_no_test_calls_is_not_known_never_untested(root):
    res = _ask(root, "divide")
    assert res.tests == [] and res.no_test_reaches == []
    assert [(n.what, n.why) for n in res.not_known] == [("divide", "no C/C++ test calls it")]
    text = render.tests_text(res)
    assert (
        "No tests to list. This does not mean no test runs this code: no C/C++ test that AeroTrace can see "
        "calls the changed functions; tests written as scripts" in text
    )
    assert "tests/extra_test.cc" in text  # the unindexed test file that does call divide


def test_depth_limit_is_said_and_never_hides_a_test_silently(root):
    res = _ask(root, "twice", depth=1)
    assert res.tests == [] and res.truncated
    [n] = res.not_known
    assert "within 1 call steps" in n.why
    assert any("Stopped at depth 1" in x for x in res.limits)
    assert "tests/test_scale.c" in _tests(_ask(root, "twice"))


def test_editing_a_test_lists_that_test(root):
    res = _ask(root, "Calc_Adds_Test::TestBody")
    assert [(t.test, t.level, t.reason) for t in res.tests] == [
        ("Calc.Adds", "exact", "this test itself changed")
    ]


def test_index_without_test_entry_points_says_so(repo_factory):
    root = repo_factory("c_multi")
    build_index(root)
    res = _ask(root, "add")
    assert {n.why for n in res.not_known} == {"no C/C++ test entry point is in the index"}
    text = render.tests_text(res)
    assert "This does not mean no test runs this code: the index has no C/C++ test entry points;" in text
    assert "can see calls" not in text


def test_test_files_that_did_not_parse_are_named(repo_factory):
    """No gtest/gtest.h found (a CMake project with no build folder, say): TEST is not expanded."""
    root = repo_factory("testimpact")
    shutil.rmtree(root / "include" / "gtest")
    build_index(root)
    res = _ask(root, "mul")
    assert not any(t.kind == "gtest" for t in res.tests)
    [limit] = [x for x in res.limits if x.startswith("Tests in these files may be missing")]
    assert "headers not found: gtest/gtest.h" in limit
    assert "tests/calc_test.cpp" in limit and "tests/layout_test.cpp" in limit


def test_unindexed_tests_under_build_folders_are_named(root):
    runner = root / "build" / "test" / "runners" / "test_calc_runner.c"
    runner.parent.mkdir(parents=True)
    runner.write_text("int main(void) { return 0; }\n")
    conn = store.connect(root)
    assert "build/test/runners/test_calc_runner.c" in testimpact.unindexed_test_files(conn, root)


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
        "tests/cmocka_more.c:split_works",
        "tests/test_scale.c",
    }
    assert set(got.values()) == {"probable"}  # linked through a changed declaration, not a call


def test_a_macro_change_is_not_known_and_the_sentence_is_true(root):
    res = _patch_res(root, "#define CALC_LIMIT 100", "#define CALC_LIMIT 200", file="include/calc.h")
    assert res.tests == []
    [n] = res.not_known
    assert n.what == "include/calc.h" and not n.followed
    assert n.why.startswith("line 6 (old and new) changed outside any function")
    text = render.tests_text(res)
    assert (
        "This does not mean no test runs this code: who uses the changes outside functions, or in files it "
        "could not read, was not followed;" in text
    )
    assert "can see calls" not in text  # macro users were never followed, so this would be false


def test_git_mode_through_the_cli(root, capsys):
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "--no-verify", "-m", "init")
    p = root / "src" / "calc.c"
    p.write_text(p.read_text().replace("return b ? a / b : 0;", "return 0;"))  # divide changes
    (root / "src" / "util.c").write_text('#include "calc.h"\n')  # test_helper is removed
    assert main(["tests", "--root", str(root), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["coverage"] == {"state": "not_recorded"}
    assert (out["changed_functions"], out["removed_functions"]) == (1, 1)
    assert out["tests"] == []
    # util.c also lost a comment line outside any function: not followed, so "Not known" too
    assert [n["what"] for n in out["not_known"]] == ["divide", "src/util.c", "test_helper (removed)"]
    assert main(["tests", "--root", str(root), "--base", "HEAD"]) == 0
    text = capsys.readouterr().out
    assert text.startswith("Tests compared with HEAD (")
    assert "1 function changed, 1 function removed in 2 C/C++ files." in text


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
