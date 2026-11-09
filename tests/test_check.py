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
    assert nodes(c)["defaults"] == ("unknown", "unknown")  # may set fields by position


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
