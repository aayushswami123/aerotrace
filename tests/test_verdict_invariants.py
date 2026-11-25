"""Two rules over every check case of test_unknown_headline.py (c_unknown, c_unseen):
the exit code is a function of the verdict, and a silent hook means the verdict is clear."""

from __future__ import annotations

import json

import pytest

from aerotrace.cli import main
from aerotrace.hooks import claude
from test_unknown_headline import (
    ADD_PARAM,
    ENUM,
    F_TWO,
    H_TWO,
    HARMLESS,
    HARMLESS_EDITS,
    MACRO,
    PRE_BROKEN,
    TYPEDEF,
    make_patch,
)
from test_unknown_headline import (
    repo as c_unknown_repo,
)
from test_unknown_headline import (
    unseen as c_unseen_repo,
)

c_unknown = c_unknown_repo  # the fixtures of test_unknown_headline.py, under new names
c_unseen = c_unseen_repo

EXPECTED = {"breaks": 1, "unknown": 2, "clear": 0}
C_UNKNOWN = {
    "macro": [MACRO],
    "typedef": [TYPEDEF],
    "enum": [ENUM],
    "pre_broken": [PRE_BROKEN],
    "harmless": [HARMLESS],
    "add_param": ADD_PARAM,
    "macro_and_break": [MACRO, *ADD_PARAM],
}
C_UNSEEN = {**HARMLESS_EDITS, "f_two": F_TWO, "h_two": H_TWO}


def run_cli(root, text, capsys):
    p = root.parent / "inv.diff"
    p.write_text(text)
    code = main(["check", "--patch", str(p), "--root", str(root), "--json"])
    return code, json.loads(capsys.readouterr().out)


def hook_out(root, edits):
    """The hook's answer for single-file edits (None when the case touches several files)."""
    if len({rel for rel, _, _ in edits}) != 1:
        return "skip"
    rel = edits[0][0]
    after = before = (root / rel).read_text()
    for _, old, new in edits:
        after = after.replace(old, new, 1)
    ev = {
        "cwd": str(root),
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": str(root / rel), "content": after},
    }
    assert after != before
    return claude.answer(ev, timeout=60.0)


def check_case(root, edits, capsys):
    code, data = run_cli(root, make_patch(root, edits), capsys)
    assert code == EXPECTED[data["verdict"]], (data["verdict"], code)
    out = hook_out(root, edits)
    if out is None:
        assert data["verdict"] == "clear", data.get("unchecked_reason")
    return data["verdict"]


@pytest.mark.parametrize("name", sorted(C_UNKNOWN))
def test_c_unknown_cases(c_unknown, capsys, name):
    check_case(c_unknown, C_UNKNOWN[name], capsys)


@pytest.mark.parametrize("name", sorted(C_UNSEEN))
def test_c_unseen_cases(c_unseen, capsys, name):
    check_case(c_unseen, C_UNSEEN[name], capsys)
