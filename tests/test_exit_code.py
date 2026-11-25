"""A-06: the exit code of `aerotrace check` is a function of the verdict: 0 clear, 1 breaks, 2 unknown.

Any part of the change that was not checked or not followed gives 2 (unless something breaks: then 1)."""

from __future__ import annotations

import difflib
import json
import subprocess

import pytest

from aerotrace.cli import main
from aerotrace.index.build import build_index

H, LIB = "include/defs.h", "src/lib.c"
MACRO = (H, "#define SCALE(x) ((x) * 2)", "#define SCALE(x, k) ((x) * (k))")  # R3
HARMLESS = (LIB, "    return a * 3;", "    return a * 4;")
ADD_PARAM = [
    (H, "int get(int k);", "int get(int k, int j);"),
    (LIB, "int get(int k) {", "int get(int k, int j) {"),
]
R6 = "\n".join(
    [
        f"--- a/{LIB}",
        f"+++ b/{LIB}",
        "@@ -1,4 +1,4 @@",
        ' #include "defs.h"',
        "-int nothing_like_this(int k) {",
        "+int get(long k) {",
        "     return k;",
        " }",
        "",
    ]
)
EXPECTED = {"breaks": 1, "unknown": 2, "clear": 0}


@pytest.fixture
def repo(repo_factory, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    root = repo_factory("c_unknown")
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "--no-verify", "-m", "init"]):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root, check=True)
    build_index(root, jobs=1)
    return root


def make_patch(root, edits):
    by_file: dict[str, list[tuple[str, str]]] = {}
    for rel, old, new in edits:
        by_file.setdefault(rel, []).append((old, new))
    out = ""
    for rel, pairs in sorted(by_file.items()):
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


def check_patch(root, text, capsys, *extra):
    p = root.parent / "p.diff"
    p.write_text(text)
    code = main(["check", "--patch", str(p), "--root", str(root), "--json", *extra])
    return code, json.loads(capsys.readouterr().out)


def test_r6_patch_that_does_not_match_exits_2(repo, capsys):
    code, data = check_patch(repo, R6, capsys)
    assert data["verdict"] == "unknown"
    assert code == 2


def test_r3_macro_arity_in_a_header_exits_2(repo, capsys):
    code, data = check_patch(repo, make_patch(repo, [MACRO]), capsys)
    assert data["verdict"] == "unknown"
    assert code == 2


def test_a_break_with_an_unchecked_part_still_exits_1(repo, capsys):
    code, data = check_patch(repo, make_patch(repo, [MACRO, *ADD_PARAM]), capsys)
    assert data["verdict"] == "breaks" and data["unchecked"] is True
    assert code == 1


def test_a_fully_checked_harmless_change_exits_0(repo, capsys):
    code, data = check_patch(repo, make_patch(repo, [HARMLESS]), capsys)
    assert data["verdict"] == "clear"
    assert code == 0


def test_git_mode_with_an_unchecked_part_exits_2(repo, capsys):
    (repo / H).write_text((repo / H).read_text().replace(MACRO[1], MACRO[2]))
    code = main(["check", "--root", str(repo), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["verdict"] == "unknown"
    assert code == 2


def test_missing_index_exits_2(repo, capsys, monkeypatch):
    def no_index(*a, **k):
        raise FileNotFoundError("compile_commands.json")

    monkeypatch.setattr("aerotrace.index.build.fresh_index", no_index)
    code, data = check_patch(repo, make_patch(repo, [HARMLESS]), capsys)
    assert data["status"] == "no_index" and data["verdict"] == "unknown"
    assert code == 2


def test_exit_code_is_a_function_of_the_verdict(repo, capsys):
    cases = [
        make_patch(repo, [MACRO]),
        make_patch(repo, [MACRO, *ADD_PARAM]),
        make_patch(repo, [HARMLESS]),
        make_patch(repo, ADD_PARAM),
        R6,
    ]
    seen = set()
    for text in cases:
        code, data = check_patch(repo, text, capsys)
        seen.add(data["verdict"])
        assert code == EXPECTED[data["verdict"]], (data["verdict"], code)
    assert seen == {"breaks", "unknown", "clear"}


def test_check_help_lists_the_three_exit_codes(capsys):
    with pytest.raises(SystemExit):
        main(["check", "--help"])
    out = capsys.readouterr().out
    assert "Exit codes" in out
    for line in ("0 ", "1 ", "2 "):
        assert any(x.strip().startswith(line) for x in out.splitlines()), line
    assert "UNKNOWN" in out


def test_top_level_help_says_2_for_unknown(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert "UNKNOWN" in out


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_mcp_check_for_r3_says_unknown_not_ok(repo, anyio_backend):
    from mcp import Client

    from aerotrace.mcp.server import create_server

    async with Client(create_server(repo)) as client:
        result = await client.call_tool("check", {"patch": make_patch(repo, [MACRO])})
    text = "\n".join(c.text for c in result.content if getattr(c, "text", None))
    first = text.splitlines()[0]
    assert "UNKNOWN" in first and "Nothing found" not in first


def raw_patch(rel, old, new):
    return f"--- a/{rel}\n+++ b/{rel}\n@@ -1 +1 @@\n-{old}\n+{new}\n"


def test_a_build_file_only_change_exits_2(repo, capsys):
    code, data = check_patch(repo, raw_patch("Makefile", "CC=cc", "CC=gcc"), capsys)
    assert data["verdict"] == "unknown" and "Makefile" in data["unchecked_reason"]
    assert code == 2


def test_a_change_with_no_c_part_exits_0_with_the_neutral_line(repo, capsys):
    p = repo.parent / "p.diff"
    p.write_text(raw_patch("README.md", "# c_unknown", "# c_unknown (docs)"))
    code = main(["check", "--patch", str(p), "--root", str(repo)])
    first = capsys.readouterr().out.splitlines()[0]
    assert code == 0
    assert "No C or C++ changes, so there was nothing to check." in first and "✅" not in first


def test_a_comment_outside_a_function_exits_0(repo, capsys):
    path = repo / LIB
    text = path.read_text()
    after = "/* a note */\n" + text
    patch = "".join(
        difflib.unified_diff(
            text.splitlines(keepends=True), after.splitlines(keepends=True), f"a/{LIB}", f"b/{LIB}"
        )
    )
    code, data = check_patch(repo, patch, capsys)
    assert data["verdict"] == "clear"
    assert code == 0
