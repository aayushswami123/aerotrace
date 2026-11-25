"""A-04: when any part of a change was not checked or not followed, the first line says UNKNOWN (or, with a
break, says that parts could not be checked). "Nothing found" is only for a change that was fully checked."""

from __future__ import annotations

import difflib
import json
import subprocess

import pytest

from aerotrace import ui
from aerotrace.analysis import check, diff
from aerotrace.index.build import build_index, fresh_index
from aerotrace.index.history import Outliner

H, LIB, BAD = "include/defs.h", "src/lib.c", "src/bad.c"
MACRO = (H, "#define SCALE(x) ((x) * 2)", "#define SCALE(x, k) ((x) * (k))")  # R3
TYPEDEF = (H, "typedef int count_t;", "typedef struct { int v; } count_t;")
ENUM = (H, "enum color { RED, GREEN, BLUE };", "enum color { RED, GREEN };")
PRE_BROKEN = (BAD, "    return a;", "    return a + 1;")
HARMLESS = (LIB, "    return a * 3;", "    return a * 4;")
ADD_PARAM = [
    (H, "int get(int k);", "int get(int k, int j);"),
    (LIB, "int get(int k) {", "int get(int k, int j) {"),
]


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


def contract_for(root, text):
    diffs = diff.load_patch(root, text)
    conn, _ = fresh_index(root, want=diff.wanted(diffs, "patch"))
    try:
        res = check.run(conn, root, diffs, mode="patch", outliner=Outliner(root))
        return check.to_contract(conn, res, "aerotrace test")
    finally:
        conn.close()


def first_lines(c):
    """The first line in Unicode and in ASCII, without the icon."""
    out = []
    for uni in (True, False):
        line = ui.render_text(c, color=False, unicode=uni).splitlines()[0]
        out.append(line.split(" ", 1)[1].lstrip())
    return out


def assert_unknown(c, reason=None):
    assert c["verdict"] == "unknown" and c["unchecked"] is True, (c.get("verdict"), c.get("unchecked"))
    for line in first_lines(c):
        assert line.startswith("UNKNOWN:"), line
        assert "Nothing found" not in line and "safe" not in line, line
        if reason:
            assert reason in line, line
    assert "✅" not in ui.render_text(c, unicode=True)


def test_r3_macro_arity_change_in_a_header_is_unknown(repo):
    c = contract_for(repo, make_patch(repo, [MACRO]))
    assert_unknown(c, f"a macro in {H}")
    assert check.exit_code(c) == 2  # A-06


def test_typedef_change_is_unknown(repo):
    assert_unknown(contract_for(repo, make_patch(repo, [TYPEDEF])), f"a type in {H}")


def test_removed_enum_value_is_unknown(repo):
    assert_unknown(contract_for(repo, make_patch(repo, [ENUM])), f"a type in {H}")


def test_edit_in_a_file_that_already_had_a_parse_error_is_unknown(repo):
    c = contract_for(repo, make_patch(repo, [PRE_BROKEN]))
    assert_unknown(c, BAD)


def test_r6_patch_that_does_not_match_the_file_is_unknown_and_not_ok(repo):
    text = "\n".join(
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
    c = contract_for(repo, text)
    assert c["status"] != "ok"
    assert c["change"]["not_followed"]["mismatched"] == [LIB]
    assert_unknown(c, LIB)


def test_a_break_with_an_unchecked_part_says_both_on_the_first_line(repo):
    c = contract_for(repo, make_patch(repo, [MACRO, *ADD_PARAM]))
    assert c["verdict"] == "breaks" and c["unchecked"] is True
    for line in first_lines(c):
        assert "BREAKS" in line and "could not be checked" in line, line
        assert "Nothing found" not in line


def test_a_fully_checked_harmless_change_keeps_the_green_line(repo):
    c = contract_for(repo, make_patch(repo, [HARMLESS]))
    assert c["verdict"] == "clear" and c["unchecked"] is False
    text = ui.render_text(c, unicode=True)
    assert text.startswith("✅ Nothing found that this change can break"), text.splitlines()[0]


def test_cli_json_has_the_verdict(repo, capsys):
    from aerotrace.cli import main

    p = repo.parent / "r3.diff"
    p.write_text(make_patch(repo, [MACRO]))
    main(["check", "--patch", str(p), "--root", str(repo), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["verdict"] == "unknown" and data["unchecked"] is True
    assert data["unchecked_parts"] >= 1 and f"a macro in {H}" in data["unchecked_reason"]
    main(["check", "--patch", str(p), "--root", str(repo)])
    first = capsys.readouterr().out.splitlines()[0]
    assert "UNKNOWN" in first and "Nothing found" not in first


def test_verdict_helper_reads_any_contract(repo):
    c = contract_for(repo, make_patch(repo, [MACRO]))
    v = check.verdict(c)
    assert (v.verdict, v.unchecked) == ("unknown", True)
    assert check.verdict(check.empty_contract("no_index", "t", "no index")).verdict == "unknown"
