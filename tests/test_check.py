"""`aerotrace check`: signature and data effects of a diff, in both modes, and the UI contract."""

from __future__ import annotations

import difflib
import json
import subprocess

import pytest

from aerotrace.analysis import check, diff
from aerotrace.index.build import build_index, fresh_index
from aerotrace.index.history import Outliner


def git(root, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture(autouse=True)
def fixed_git_dates(monkeypatch):
    for k in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"):
        monkeypatch.setenv(k, "2026-01-01T00:00:00Z")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")


@pytest.fixture
def repo(repo_factory):
    root = repo_factory("c_check")
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "--no-verify", "-m", "init")
    build_index(root, jobs=1)
    return root


def patch(root, edits):
    """A unified diff for edits {file: [(old, new), ...]}, made without touching the files."""
    out = ""
    for rel, pairs in edits.items():
        before = (root / rel).read_text()
        after = before
        for old, new in pairs:
            assert old in after, old
            after = after.replace(old, new, 1)
        out += "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True), after.splitlines(keepends=True), f"a/{rel}", f"b/{rel}"
            )
        )
    return out


def run_patch(root, edits):
    diffs = diff.load_patch(root, patch(root, edits))
    conn, _ = fresh_index(root, want=diff.wanted(diffs, "patch"))
    res = check.run(conn, root, diffs, mode="patch", outliner=Outliner(root))
    return res, check.to_contract(conn, res, "aerotrace test")


def run_git(root, edits):
    for rel, pairs in edits.items():
        p = root / rel
        text = p.read_text()
        for old, new in pairs:
            assert old in text, old
            text = text.replace(old, new, 1)
        p.write_text(text)
    outliner = Outliner(root)
    diffs, desc, old = diff.load_git(root, "HEAD", outliner, every=True)
    conn, _ = fresh_index(root, want=diff.wanted(diffs, "git"))
    res = check.run(conn, root, diffs, mode="git", base=desc, outliner=outliner, old=old)
    return res, check.to_contract(conn, res, "aerotrace test")


def nodes(contract, status=None):
    return {
        n["name"]: (n["status"], n["certainty"])
        for n in contract["nodes"]
        if status is None or n["status"] == status
    }


def blind(contract):
    return [b["text"] for b in contract["sections"]["blind_spots"]]


H, A, U = "include/api.h", "src/api.c", "src/use.c"
ADD_PARAM = {
    H: [("int parse(int x);", "int parse(int x, int y);")],
    A: [("int parse(int x) {", "int parse(int x, int y) {")],
}


# ---------------------------------------------------------------- signatures


@pytest.mark.parametrize("mode", ["patch", "git"])
def test_added_parameter_breaks_every_call(repo, mode):
    res, c = (run_patch if mode == "patch" else run_git)(repo, ADD_PARAM)
    got = nodes(c)
    assert got["use_parse"] == ("breaks", "exact")
    assert got["parse"][0] == "changed"
    # stored in a table of the old pointer type: an error on clang 16+ / GCC 14+, so a probable break
    assert got["handlers"] == ("breaks", "probable")
    if mode == "patch":
        assert got["dispatch"] == ("affected", "probable")  # calls a pointer of the old type
    assert [s.kinds for s in res.signatures] == [["params_added"]]
    assert c["summary"]["breaks_exact"] >= 1
    assert check.exit_code(c) == 1


def test_reordered_parameters_affect_callers_but_do_not_break(repo):
    _, c = run_patch(repo, {A: [("int scale(int a, int b) {", "int scale(int b, int a) {")]})
    got = nodes(c)
    assert got["use_scale"] == ("affected", "exact")
    assert "swap" in next(n["why"] for n in c["nodes"] if n["name"] == "use_scale")
    assert c["summary"]["breaks_exact"] == c["summary"]["breaks_probable"] == 0


def test_retyped_parameter_is_affected_not_a_break(repo):
    _, c = run_patch(
        repo,
        {
            A: [("int parse(int x) {", "int parse(long x) {")],
            H: [("int parse(int x);", "int parse(long x);")],
        },
    )
    assert nodes(c)["use_parse"] == ("affected", "exact")  # int -> long converts: the call still builds
    assert nodes(c, "breaks") == {"handlers": ("breaks", "probable")}  # the pointer type no longer matches
    assert not any("conflicting types" in t for t in blind(c))  # .h and .c parsed together


def test_renaming_a_parameter_is_not_a_signature_change(repo):
    res, c = run_patch(
        repo, {A: [("int parse(int x) {\n    return x + 1;", "int parse(int v) {\n    return v + 1;")]}
    )
    assert res.signatures == []
    assert not nodes(c, "breaks")


def test_void_return_breaks_calls_that_use_the_result(repo):
    edits = {
        H: [("int status(void);", "void status(void);")],
        A: [("int status(void) {\n    return 0;", "void status(void) {\n    return;")],
    }
    _, c = run_patch(repo, edits)
    assert nodes(c)["use_status"] == ("breaks", "exact")


def test_no_prototype_never_breaks(repo):
    # K&R definition and `int old_style();`: any argument count compiles
    _, c = run_patch(
        repo, {A: [("int old_style(x)\n    int x;", "int old_style(x, y)\n    int x;\n    int y;")]}
    )
    assert nodes(c)["use_old"] == ("affected", "probable")


def test_variadic_gets_a_fixed_parameter(repo):
    edits = {
        H: [("int log_msg(const char *fmt, ...);", "int log_msg(int level, const char *fmt, ...);")],
        A: [("int log_msg(const char *fmt, ...) {", "int log_msg(int level, const char *fmt, ...) {")],
    }
    _, c = run_patch(repo, edits)
    assert nodes(c)["use_log"] == ("breaks", "exact")  # log_msg("x") passes 1 of 2


def test_static_function_with_the_same_name_elsewhere_is_not_mixed_up(repo):
    _, c = run_patch(
        repo, {"src/other.c": [("static int helper(int x) {", "static int helper(int x, int y) {")]}
    )
    got = nodes(c)
    assert got["other_user"] == ("breaks", "exact")
    assert "third_user" not in got


# ---------------------------------------------------------------- declarations


def test_retyped_field_affects_its_users(repo):
    _res, c = run_patch(repo, {H: [("    int count;", "    long count;")]})
    got = nodes(c)
    assert got["read_count"] == ("affected", "exact") and got["write_count"] == ("affected", "exact")
    assert not nodes(c, "breaks")
    assert c["change"]["not_followed"]["outside"] == []  # #13


def test_bitfield_width_change_is_never_an_all_clear(repo):
    _, c = run_patch(repo, {H: [("unsigned flags : 3;", "unsigned flags : 5;")]})
    assert "read_flags" in nodes(c)
    assert any("declaration text changed" in t for t in blind(c))
    assert c["summary"]["not_followed"] > 0


@pytest.mark.parametrize("mode", ["patch", "git"])
def test_removed_field_breaks_its_users(repo, mode):
    _res, c = (run_patch if mode == "patch" else run_git)(repo, {H: [("    long total;\n", "")]})
    got = nodes(c)
    assert got["read_total"] == ("breaks", "exact")
    assert got["conn::total"][0] == "removed"
    assert any("size and layout" in t for t in blind(c))
    # `defaults = {1, 2, 0, 3}` sets total by position (#26): seen in the index before the change (patch);
    # after it (the index of the changed code), the values shift and the global is still "may set by position"
    want = ("breaks", "exact") if mode == "patch" else ("unknown", "unknown")
    assert nodes(c)["defaults"] == want


def test_renamed_field_is_removed_and_added_with_a_hint(repo):
    _, c = run_patch(repo, {H: [("    int count;", "    int n;")]})
    got = nodes(c)
    assert got["read_count"] == ("breaks", "exact") and got["write_count"] == ("breaks", "exact")
    removed = next(n for n in c["nodes"] if n["name"] == "conn::count")
    assert removed["status"] == "removed" and "maybe renamed to n" in removed["why"]
    assert removed["id"].startswith("removed:")


def test_initial_value_change_affects_readers(repo):
    _, c = run_patch(repo, {A: [("int max_clients = 1000;", "int max_clients = 10;")]})
    assert nodes(c)["read_max"] == ("affected", "probable")


# ---------------------------------------------------------------- compile errors, gaps, contract


def test_definition_that_no_longer_matches_its_prototype_is_a_break(repo):
    # the changed function itself does not compile: it is a break, never only a note (review of #48)
    _, c = run_patch(repo, {A: [("int scale(int a, int b) {", "int scale(long a, int b) {")]})
    scale = next(n for n in c["nodes"] if n["name"] == "scale" and n["status"] == "breaks")
    assert scale["certainty"] == "exact"
    assert "conflicting types" in scale["why"]
    assert check.exit_code(c) == 1 and c["summary"]["breaks_exact"] >= 1
    # #49: it is still listed as the change itself (distance 0, no link), saying it does not build
    [change] = [n for n in c["nodes"] if n["name"] == "scale" and n["distance"] == 0]
    assert change["status"] == "changed" and change["link"] is None
    assert "does not compile" in change["why"]
    assert scale["distance"] == 1 and scale["id"] == change["id"] + "#breaks"


def test_caller_edited_by_the_patch_but_still_broken_is_a_break(repo):
    # review of #48, finding 1: the patch edits use_parse, but leaves its calls with one argument
    edits = {**ADD_PARAM, U: [("int use_parse(int v) {", "int use_parse(int v) { /* edited */")]}
    _, c = run_patch(repo, edits)
    assert nodes(c)["use_parse"] == ("breaks", "exact")
    assert check.exit_code(c) == 1


def test_caller_fixed_by_the_patch_is_not_a_break(repo):
    edits = {**ADD_PARAM, U: [("return parse(v) + PARSE_TWICE(v);", "return parse(v, 0);")]}
    _, c = run_patch(repo, edits)
    assert "use_parse" not in nodes(c, "breaks")


def test_removed_field_whose_user_is_edited_but_still_uses_it(repo):
    edits = {
        H: [("    long total;\n", "")],
        U: [("long read_total(void) {", "long read_total(void) { /* x */")],
    }
    _, c = run_patch(repo, edits)
    assert nodes(c)["read_total"] == ("breaks", "exact")


def test_knr_to_prototype_in_header_and_definition_breaks_old_calls(repo):
    # review of #48, finding 2: the patched .c must be parsed with the patched .h
    edits = {
        H: [("int old_style();", "int old_style(int x, int y);")],
        A: [("int old_style(x)\n    int x;\n{", "int old_style(int x, int y) {")],
    }
    _, c = run_patch(repo, edits)
    assert nodes(c)["use_old"] == ("breaks", "exact")


def test_patch_that_does_not_match_is_counted(repo):
    text = patch(repo, {A: [("    return x + 1;", "    return x + 2;")]})
    (repo / A).write_text((repo / A).read_text().replace("#include", "\n#include", 1))
    diffs = diff.load_patch(repo, text)
    conn, _ = fresh_index(repo, want=diff.wanted(diffs, "patch"))
    res = check.run(conn, repo, diffs, mode="patch", outliner=Outliner(repo))
    c = check.to_contract(conn, res, "t")
    assert c["change"]["not_followed"]["mismatched"] == [A] and c["summary"]["not_followed"] > 0


def test_moving_lines_changes_no_signature_or_declaration(repo):
    # unnamed unions carry their line in their name, and a prototype may spell a type another way
    # (conn_t * vs struct conn *): neither is a change
    res, c = run_patch(
        repo,
        {H: [("#define API_H\n", "#define API_H\n/* moved */\n")], A: [("#include", "/* x */\n#include")]},
    )
    assert res.signatures == [] and res.decls == []
    assert not nodes(c, "breaks") and not nodes(c, "affected")


def test_comment_on_a_declaration_line_changes_nothing(repo):
    _, c = run_patch(repo, {A: [("int max_clients = 1000;", "int max_clients = 1000; /* max */")]})
    assert [n["status"] for n in c["nodes"]] == ["changed"]
    assert c["summary"]["not_followed"] == 0


def test_cli_check_json_and_exit_code(repo, capsys):
    from aerotrace.cli import main

    (repo / "p.diff").write_text(patch(repo, ADD_PARAM))
    code = main(["check", "--patch", str(repo / "p.diff"), "--root", str(repo), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert code == 1 and data["format"] == "aerotrace-check" and data["summary"]["breaks_exact"] >= 1
    code = main(["check", "--root", str(repo)])
    out = capsys.readouterr().out
    assert code == 0 and "No C or C++ changes to check" in out  # p.diff itself is not code


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_mcp_check_tool(repo):
    from mcp import Client

    from aerotrace.mcp.server import create_server

    async with Client(create_server(repo)) as client:
        result = await client.call_tool("check", {"patch": patch(repo, ADD_PARAM)})
    text = "\n".join(c.text for c in result.content if getattr(c, "text", None))
    assert "use_parse" in text and "BREAKS" in text


def test_output_is_deterministic_and_follows_the_contract(repo):
    _, c1 = run_patch(repo, ADD_PARAM)
    _, c2 = run_patch(repo, ADD_PARAM)
    assert json.dumps(c1, sort_keys=True) == json.dumps(c2, sort_keys=True)
    ui = pytest.importorskip("aerotrace.ui")
    ui.check(c1)
    assert "use_parse" in ui.render_text(c1)
    assert "use_parse" in ui.render_html(c1)


# ---------------------------------------------------------------- coverage of the build


@pytest.fixture
def repo_with_outside_file(repo_factory):
    root = repo_factory("c_check")
    # written after compile_commands.json, so the build does not list it
    (root / "legacy").mkdir()
    (root / "legacy" / "old.c").write_text(
        '#include "api.h"\nint old_use(void) { return parse(1) + lonely(2); }\n'
    )
    # lonely() is called only from legacy/old.c, which is outside the build
    api = root / "src" / "api.c"
    api.write_text(api.read_text() + "\nint lonely(int x) {\n    return x;\n}\n")
    h = root / "include" / "api.h"
    h.write_text(h.read_text().replace("int parse(int x);", "int parse(int x);\nint lonely(int x);"))
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "--no-verify", "-m", "init")
    build_index(root, jobs=1)
    return root


def test_coverage_counts_the_build_and_what_is_outside_it(repo_with_outside_file):
    root = repo_with_outside_file
    _, c = run_patch(root, ADD_PARAM)
    p = c["proof"]
    n_units = len(json.loads((root / "compile_commands.json").read_text()))
    assert (p["build_files"], p["build_files_checked"], p["build_files_with_errors"]) == (n_units, n_units, 0)
    assert p["outside_build"] == 1
    assert "outside_build_files" not in p  # only with list_outside
    # blind spot: the caller in legacy/old.c is outside the build, so it is not listed as a break...
    assert "old_use" not in nodes(c)
    # ...and the long index limit is replaced by the coverage line, which still says it was not checked
    assert not any("not in compile_commands.json" in t for t in blind(c))
    ui = pytest.importorskip("aerotrace.ui")
    text = ui.render_text(c, all_hint="--all lists them")
    assert f"Checked {n_units} of {n_units} files in this build." in text
    # every file is named (there is only one), so there is nothing more for --all to list
    assert "1 C/C++ file outside this build was not checked: legacy/old.c." in text
    assert "outside this build was not checked: legacy/old.c." in ui.render_html(c)


def test_a_pass_shows_the_files_outside_the_build_right_below_the_headline(repo_with_outside_file):
    # blind spot: lonely() has one caller, in legacy/old.c, which the build does not list. AeroTrace can't
    # see it, so it finds nothing to break; the answer must say, right below the headline, what it missed.
    _, c = run_patch(
        repo_with_outside_file,
        {A: [("int lonely(int x) {\n    return x;", "int lonely(int x) {\n    return x * 2;")]},
    )
    assert "old_use" not in nodes(c)
    ui = pytest.importorskip("aerotrace.ui")
    lines = ui.render_text(c, all_hint="--all lists them").splitlines()
    assert lines[0].startswith("✅"), lines[0]
    top = "\n".join(lines[:6])
    assert "1 C/C++ file outside this build was not checked: legacy/old.c." in top
    assert sum("outside this build" in line for line in lines) == 1  # not repeated at the end
    html = ui.render_html(c)
    assert html.count("outside this build was not checked: legacy/old.c.") == 1


def test_list_outside_names_the_files(repo_with_outside_file):
    root = repo_with_outside_file
    diffs = diff.load_patch(root, patch(root, ADD_PARAM))
    conn, _ = fresh_index(root, want=diff.wanted(diffs, "patch"))
    res = check.run(conn, root, diffs, mode="patch", outliner=Outliner(root))
    c = check.to_contract(conn, res, "aerotrace test", list_outside=True)
    assert c["proof"]["outside_build_files"] == ["legacy/old.c"]
    ui = pytest.importorskip("aerotrace.ui")
    ui.check(c)
    text = ui.render_text(c, all_hint="--all lists them")
    assert "Files outside this build (1)\n  legacy/old.c" in text
    assert "(--all lists them)" not in text  # already listed


# ---------------------------------------------------------------- removed field used at file scope (#57)


@pytest.fixture
def init_repo(repo_factory):
    root = repo_factory("c_removed_init")
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "--no-verify", "-m", "init")
    build_index(root, jobs=1)
    return root


REMOVE_HZ = {"include/conn.h": [("    int a;\n    int hz;\n", "    int a;\n")]}


@pytest.mark.parametrize("mode", ["patch", "git"])
def test_removed_field_breaks_the_initializers_that_use_it(init_repo, mode):
    _, c = (run_patch if mode == "patch" else run_git)(init_repo, REMOVE_HZ)
    got = nodes(c)
    # proof in the index: table stores &g.hz on line 16, defaults sets .hz on line 20
    assert got["table"] == ("breaks", "exact"), got
    assert got["defaults"] == ("breaks", "exact"), got
    assert check.exit_code(c) == 1
    by = {n["name"]: n for n in c["nodes"]}
    if mode == "git":
        assert "its initializer at src/tables.c:16 does not compile now:" in by["table"]["why"]
        assert all("does not compile now: does not compile now" not in n["why"] for n in c["nodes"])


def test_file_scope_error_with_no_proof_is_probable_and_stays_a_blind_spot(init_repo):
    _, c = run_git(init_repo, REMOVE_HZ)
    by = {n["name"]: n for n in c["nodes"]}
    spots = [t for t in blind(c) if "does not compile now" in t]
    # line 26, a static assert: no initializer row there, so only the nearest global above it (counter),
    # probable. The line does not compile either way, so it is a break, and it stays a blind spot too.
    assert (by["counter"]["status"], by["counter"]["certainty"]) == ("breaks", "probable")
    assert (
        "probably its initializer (the nearest global above that line) at src/tables.c:26"
        in by["counter"]["why"]
    )
    assert any(t.startswith("src/tables.c:26 ") for t in spots), spots
    # line 29, two globals on one line: the index can't tell which one, so both are probable, never exact
    for name in ("x", "y"):
        assert (by[name]["status"], by[name]["certainty"]) == ("breaks", "probable"), by[name]
        assert "one of 2 globals on that line" in by[name]["why"]
    assert any(t.startswith("src/tables.c:29 ") for t in spots), spots
    # line 4 comes before every global, so there is no global to name: a blind spot only
    assert any(t.startswith("src/tables.c:4 ") for t in spots), spots
    # the proven lines (16, 20) are not blind spots; the three unproven ones are, each once
    assert sorted(t.split(" ", 1)[0] for t in spots) == [
        "src/tables.c:26",
        "src/tables.c:29",
        "src/tables.c:4",
    ]
    assert by["table"]["certainty"] == by["defaults"]["certainty"] == "exact"


def test_removed_global_used_at_file_scope_is_listed_once(init_repo):
    _, c = run_git(
        init_repo,
        {
            "include/conn.h": [("extern int rate;\n", "")],
            "src/tables.c": [("int rate = 5;\n", "")],
        },
    )
    by = {n["name"]: n for n in c["nodes"]}
    assert (by["pr"]["status"], by["pr"]["certainty"]) == ("breaks", "exact"), by["pr"]
    # gone_fields finds rate twice (the extern and the definition); the reason is given once
    assert by["pr"]["why"].count("does not compile now") == 1, by["pr"]["why"]
    assert all(n["why"].count("does not compile now") <= 1 for n in c["nodes"])


def test_two_removed_names_on_one_line_both_link_to_it(init_repo):
    # line 37: int t = sizeof(g.hz) + rate; removing both breaks t through each of them
    _, c = run_git(
        init_repo,
        {
            "include/conn.h": [("    int a;\n    int hz;\n", "    int a;\n"), ("extern int rate;\n", "")],
            "src/tables.c": [("int rate = 5;\n", "")],
        },
    )
    [t] = [n for n in c["nodes"] if n["name"] == "t" and n["status"] == "breaks"]
    assert t["certainty"] == "exact"
    assert "other link" in t["why"], t["why"]  # the second removed name is not dropped
