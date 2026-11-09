"""Recorded coverage: `aerotrace tests record` and how `aerotrace tests` uses tests.json.

The fixture `covproj` builds a tiny program with `cc --coverage` (its own build.sh) and has three tests:
`./build/prog a` runs fa, `b` runs fb, `abort` runs fa and then calls abort(), so its counts are lost.
Nobody runs fx. `twice` is an inline function in a header used from two files; `pick` is a static that
-O2 inlines into main.

These tests need a C compiler and gcov (CI has both). The parsers are also tested on sample text.
"""

import json
import shutil
import subprocess
import sys

import pytest

from aerotrace.analysis import coverage, render, symbols, testimpact
from aerotrace.cli import main
from aerotrace.index import store
from aerotrace.toolchain import testrun
from test_diff import git

needs_gcov = pytest.mark.skipif(
    not shutil.which("cc") or not (shutil.which("gcov") or shutil.which("llvm-cov")),
    reason="needs cc and gcov",
)


# ---------------------------------------------------------------- parsing gcov output (no tools needed)


def test_parse_llvm_intermediate():
    text = """file:t_string.c
function:26,13,checkStringLength
function:456,0,psetexCommand
function:10,4,_ZN2ns1A1fEi
file:sds.h
function:60,9,sdslen
"""
    got = coverage.parse_llvm_intermediate(text)
    assert [(f.source, f.name, f.line, f.count) for f in got] == [
        ("t_string.c", "checkStringLength", 26, 13),
        ("t_string.c", "psetexCommand", 456, 0),
        ("t_string.c", "_ZN2ns1A1fEi", 10, 4),
        ("sds.h", "sdslen", 60, 9),
    ]


def test_parse_gnu_json():
    doc = {
        "format_version": "1",
        "current_working_directory": "/w",
        "files": [
            {
                "file": "src/a.cc",
                "functions": [
                    {"name": "_Z3fooi", "demangled_name": "foo(int)", "start_line": 3, "end_line": 5,
                     "execution_count": 2},
                    {"name": "bar.constprop.0", "demangled_name": "bar.constprop.0", "start_line": 9,
                     "end_line": 9, "execution_count": 0},
                    {"name": "_ZN2ns1BIiE3getEv", "demangled_name": "ns::B<int>::get() const",
                     "start_line": 12, "end_line": 12, "execution_count": 1},
                ],
            }
        ],
    }  # fmt: skip
    got = coverage.parse_gnu_json(json.dumps(doc))
    assert [(f.name, f.line, f.end_line, f.count) for f in got] == [
        ("foo", 3, 5, 2),
        ("bar", 9, 9, 0),
        ("ns::B<int>::get", 12, 12, 1),
    ]


@pytest.mark.parametrize(
    "raw, clean",
    [
        ("foo.constprop.0", "foo"),
        ("foo.isra.0.part.1", "foo"),
        ("foo.cold", "foo"),
        ("ns::A::f(int) const", "ns::A::f"),
        ("std::map<int, (anonymous)>::at(int)", "std::map<int, (anonymous)>::at"),
    ],
)
def test_clean_name(raw, clean):
    assert coverage.clean_name(raw) == clean


def test_match_uses_line_ranges_and_folds_lambdas():
    f = coverage.IndexFile("a.cc", [("outer", 1, 20), ("inner", 5, 8), ("other", 30, 40)])
    assert coverage.match([f], "inner", 5) == "inner"
    assert coverage.match([f], "operator()", 6) == "inner"  # a lambda inside inner
    assert coverage.match([f], "outer", 2) == "outer"
    assert coverage.match([f], "gone", 25) is None


def test_other_version_is_not_read(tmp_path):
    (tmp_path / ".aerotrace").mkdir()
    (tmp_path / ".aerotrace" / "tests.json").write_text(
        json.dumps({"format": "aerotrace-tests", "version": 99})
    )
    data, note = coverage.load(tmp_path)
    assert data is None and "format version 99" in note


# ---------------------------------------------------------------- config


def _write(tmp_path, text):
    p = tmp_path / "tests.toml"
    p.write_text(text)
    return p


@pytest.mark.parametrize(
    "text, msg",
    [
        ('version = 1\nrun = "x"\ntests = ["a"]', "must contain {test}"),
        ('version = 1\nrun = "x {test}"', "either `tests`"),
        ('version = 1\nrun = "x {test}"\ntests = ["a"]\nlist = "ls"', "either `tests`"),
        ('version = 1\nrun = "x {test}"\ntests = ["a"]\nsurprise = 1', "unknown setting"),
        ('run = "x {test}"\ntests = ["a"]', "version = 1"),
        ('version = 1\nrun = "x {test}"\ntests = ["a"]\nrun_many = "x"', "run_many"),
    ],
)
def test_bad_configs_are_refused(tmp_path, text, msg):
    with pytest.raises(testrun.ConfigError, match=msg):
        testrun.load_config(_write(tmp_path, text))


def test_test_ids_are_quoted_for_the_shell():
    assert testrun.command_for("./run {test}", "a b; rm -rf /") == "./run 'a b; rm -rf /'"
    assert testrun.ready_command("./r {test}", "./r {tests}", "--single {test}", ["x", "y z"]) == [
        "./r --single x --single 'y z'"
    ]


# ---------------------------------------------------------------- recording (needs cc and gcov)


@pytest.fixture
def proj(repo_factory, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    for k in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"):
        monkeypatch.setenv(k, "2026-01-01T00:00:00Z")
    root = repo_factory("covproj")
    (root / ".aerotrace").mkdir(exist_ok=True)
    shutil.copy(root / "tests.toml", root / ".aerotrace" / "tests.toml")
    return root


def _record(root, *extra):
    return main(["tests", "record", "--root", str(root), "--yes", *extra])


def _ask(root, name):
    conn = store.connect(root)
    try:
        [fn] = symbols.find(conn, name)
        return testimpact.for_function(conn, root, fn)
    finally:
        conn.close()


def _tests(res):
    return {t.test: t.level for t in res.tests}


@needs_gcov
def test_record_attributes_each_test_and_writes_nothing_else(proj, capsys):
    assert _record(proj) == 0
    out = capsys.readouterr().out
    assert "Config: " in out and "SHA-256" in out and "sh build.sh" in out  # the plan, even with --yes
    assert sorted(p.name for p in proj.rglob("*.gcda")) == []  # GCOV_PREFIX kept counts out of the repo
    assert sorted(p.name for p in (proj / ".aerotrace").iterdir() if p.name.startswith("tests")) == [
        "tests.json",
        "tests.toml",
    ]
    assert _tests(_ask(proj, "fa")) == {"a": "exact"}  # 'abort' ran fa too, but its counts died with it
    assert _tests(_ask(proj, "fb")) == {"b": "exact"}
    assert _tests(_ask(proj, "twice")) == {"a": "exact", "b": "exact"}  # header inline, summed over files
    assert _tests(_ask(proj, "pick")) == {"a": "exact", "b": "exact"}  # inlined at -O2, still counted
    fa = render.tests_text(_ask(proj, "fa"))
    assert "Ready to run:\n  ./build/prog a" in fa
    assert "abort()" in fa and "(abort)" in fa  # the limits say why the abort test is missing


@needs_gcov
def test_unrun_code_is_no_test_reaches_but_only_probable(proj):
    _record(proj)
    res = _ask(proj, "fx")
    assert res.tests == [] and res.not_known == []
    [n] = res.no_test_reaches
    assert n.what == "fx" and n.why.startswith("probable:") and "none of the 3 recorded runs" in n.why
    text = render.tests_text(res)
    assert "No test reaches this code (1):" in text
    assert "This does not mean no test runs this code" in text


@needs_gcov
def test_edited_file_makes_coverage_outdated(proj):
    _record(proj)
    lib = proj / "src" / "lib.c"
    lib.write_text(lib.read_text().replace("return x * 3;", "return x * 4;"))
    res = _ask(proj, "fx")  # symbol mode: no base side, so the file no longer matches
    assert res.no_test_reaches == []
    [n] = res.not_known
    assert "changed since coverage was recorded" in n.why
    assert _tests(_ask(proj, "fa")) == {"a": "probable"}
    assert res.coverage == "outdated"


@needs_gcov
def test_git_mode_base_side_keeps_coverage_fresh_and_new_functions_are_unknown(proj, capsys):
    git(proj, "init", "-q", "-b", "main")
    git(proj, "add", "-A")
    git(proj, "commit", "-q", "--no-verify", "-m", "init")
    _record(proj)
    capsys.readouterr()
    lib = proj / "src" / "lib.c"
    lib.write_text(
        lib.read_text().replace("return twice(x) + 1;", "return twice(x) + 9;")
        + "\nint fnew(int x) { return x; }\n"
    )
    assert main(["tests", "--root", str(proj), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["coverage"]["state"] == "fresh"  # the recorded content is the base of the change
    assert {t["id"]: (t["level"], t["source"]) for t in out["tests"]} == {"a": ("exact", "coverage")}
    [nk] = out["not_known"]
    assert nk["what"] == "fnew" and "new; no coverage" in nk["why"]
    assert out["no_test_reaches"] == []


@needs_gcov
def test_partial_runs_never_replace_a_complete_record(proj, capsys):
    assert _record(proj, "--only", "a") == 0  # nothing complete yet: goes to tests.partial.json
    assert not coverage.path(proj).exists() and coverage.path(proj, partial=True).exists()
    res = _ask(proj, "fx")
    assert res.no_test_reaches == [] and res.coverage == "partial"
    assert "partial" in res.not_known[0].why
    assert _record(proj) == 0  # complete: replaces the partial file
    full = coverage.path(proj).read_text()
    assert not coverage.path(proj, partial=True).exists()
    assert _record(proj, "--only", "b") == 0  # same basis: merged into the complete file
    assert json.loads(coverage.path(proj).read_text())["complete"] is True
    assert coverage.path(proj).read_text() == full  # b's coverage was the same, so nothing changed
    (proj / "src" / "lib.c").write_text((proj / "src" / "lib.c").read_text() + "\n")
    assert _record(proj, "--only", "b") == 0  # the code changed: the complete file is kept as it was
    assert coverage.path(proj).read_text() == full
    assert coverage.path(proj, partial=True).exists()


@needs_gcov
def test_recording_twice_gives_the_same_file(proj):
    _record(proj)
    first = coverage.path(proj).read_bytes()
    _record(proj)
    assert coverage.path(proj).read_bytes() == first


@needs_gcov
def test_changed_test_inputs_downgrade_no_test_reaches(proj):
    _record(proj)
    (proj / "tests" / "cases.txt").write_text("a\nb\nabort\nx\n")
    res = _ask(proj, "fx")
    assert res.no_test_reaches == []
    assert "test files changed since recording" in res.not_known[0].why


@needs_gcov
def test_no_terminal_without_yes_runs_nothing(proj, capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", open("/dev/null"))  # noqa: SIM115 - not a terminal
    assert main(["tests", "record", "--root", str(proj)]) == 1
    assert "Pass --yes" in capsys.readouterr().out
    assert not (proj / "build").exists()  # the build did not run
    assert not coverage.path(proj).exists()


@needs_gcov
def test_user_gcov_prefix_is_removed_and_said(proj, capsys, monkeypatch):
    monkeypatch.setenv("GCOV_PREFIX", str(proj / "elsewhere"))
    assert _record(proj) == 0
    assert "Removed from the environment for these runs: GCOV_PREFIX." in capsys.readouterr().out
    assert not (proj / "elsewhere").exists()
    assert _tests(_ask(proj, "fa")) == {"a": "exact"}


def test_dry_run_and_missing_config(proj, capsys):
    assert main(["tests", "record", "--root", str(proj), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Then, to list the tests: cat tests/cases.txt" in out
    assert not (proj / "build").exists()
    (proj / ".aerotrace" / "tests.toml").unlink()
    assert main(["tests", "record", "--root", str(proj), "--yes"]) == 2
    assert "AeroTrace never guesses what to run" in capsys.readouterr().err


@needs_gcov
def test_status(proj, capsys):
    assert main(["tests", "status", "--root", str(proj)]) == 0
    assert "No coverage recorded" in capsys.readouterr().out
    _record(proj)
    capsys.readouterr()
    (proj / "src" / "lib.c").write_text((proj / "src" / "lib.c").read_text() + "\n")
    assert main(["tests", "status", "--root", str(proj)]) == 0
    out = capsys.readouterr().out
    assert "3 test(s) recorded, 3 source file(s) with coverage." in out
    assert "Changed since recording: 1 source file(s): src/lib.c" in out
    assert "Did not pass when recorded: abort" in out


def test_gcov_is_never_run_on_the_source_tree(proj):
    """The build and tests write where they like; AeroTrace itself only writes .aerotrace/."""
    before = subprocess.run(["ls", "-R", str(proj)], capture_output=True, text=True).stdout
    main(["tests", "--root", str(proj), "--symbol", "fa"])
    after = subprocess.run(["ls", "-R", str(proj)], capture_output=True, text=True).stdout
    assert [ln for ln in after.splitlines() if ln not in before.splitlines() and ".aerotrace" not in ln] == []
