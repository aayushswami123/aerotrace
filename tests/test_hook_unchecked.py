"""A-09: the hook is never silent when it could not check. Silent only means: checked, nothing to say."""

from __future__ import annotations

import json
import subprocess

import pytest

from aerotrace.analysis import check, diff
from aerotrace.hooks import claude
from aerotrace.index.build import build_index, fresh_index
from aerotrace.index.history import Outliner

H, LIB = "include/defs.h", "src/lib.c"
MACRO_OLD, MACRO_NEW = "#define SCALE(x) ((x) * 2)", "#define SCALE(x, k) ((x) * (k))"


@pytest.fixture
def repo(repo_factory, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    root = repo_factory("c_unknown").resolve()
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "--no-verify", "-m", "init"]):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root, check=True)
    build_index(root, jobs=1)
    return root


def event(root, tool, rel, **inp):
    return {
        "session_id": "s",
        "transcript_path": "/dev/null",
        "cwd": str(root),
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": {"file_path": str(root / rel), **inp},
        "tool_use_id": "t",
    }


def run(ev):
    out = claude.answer(ev, timeout=60.0)
    if out is not None:
        assert json.loads(json.dumps(out)) == out
        assert "permissionDecision" not in out["hookSpecificOutput"]  # warns, never blocks
    return out


def lines(out):
    return [x for x in out["hookSpecificOutput"]["additionalContext"].splitlines() if x.strip()]


def test_r3_macro_edit_in_a_header_prints_one_line(repo):
    out = run(event(repo, "Edit", H, old_string=MACRO_OLD, new_string=MACRO_NEW))
    assert out is not None
    (line,) = lines(out)
    assert "could not check" in line and f"a macro in {H}" in line and "Build" in line
    assert "Traceback" not in line


def test_unindexed_c_file_prints_one_line(repo):
    (repo / "src/extra.c").write_text("int extra(int a) {\n    return a;\n}\n")
    out = run(event(repo, "Edit", "src/extra.c", old_string="return a;", new_string="return a + 1;"))
    assert out is not None
    (line,) = lines(out)
    assert "src/extra.c is not in the index" in line and "could not check" in line


@pytest.mark.parametrize("rel", ["README.md", "tool.py", "data.json"])
def test_non_c_files_stay_silent(repo, rel):
    (repo / rel).write_text("hello\n")
    assert run(event(repo, "Edit", rel, old_string="hello", new_string="bye")) is None


def test_def_table_file_prints_one_line(repo):
    (repo / "cmds.def").write_text("CMD(get)\n")
    out = run(event(repo, "Edit", "cmds.def", old_string="CMD(get)", new_string="CMD(put)"))
    assert out is not None
    (line,) = lines(out)
    assert "cmds.def" in line and "could not check" in line


def test_fully_checked_harmless_edit_is_silent(repo):
    out = run(event(repo, "Edit", LIB, old_string="return a * 3;", new_string="return a * 4;"))
    assert out is None


def test_failed_refresh_prints_one_line_and_no_traceback(repo, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr("aerotrace.index.build.build_index", boom)
    (repo / LIB).write_text((repo / LIB).read_text() + "\n/* touched */\n")
    out = run(event(repo, "Edit", LIB, old_string="return a * 3;", new_string="return a * 4;"))
    assert out is not None
    (line,) = lines(out)
    assert "could not check" in line.lower().replace("couldn't", "could not")
    assert "Traceback" not in line and "\n" not in line


def test_a_break_keeps_its_message_and_adds_the_unchecked_clause(repo):
    new = (
        (repo / H)
        .read_text()
        .replace(MACRO_OLD, MACRO_NEW)
        .replace("int get(int k);", "int get(int k, int j);")
    )
    out = run(event(repo, "Write", H, content=new))
    assert out is not None
    text = out["hookSpecificOutput"]["additionalContext"]
    assert "could not be checked" in text


def test_a_silent_hook_means_a_clear_verdict(repo):
    cases = [
        (H, MACRO_OLD, MACRO_NEW),
        (LIB, "return a * 3;", "return a * 4;"),
        (LIB, "return k;", "return k + 1;"),
        (H, "typedef int count_t;", "typedef struct { int v; } count_t;"),
        (H, "enum color { RED, GREEN, BLUE };", "enum color { RED, GREEN };"),
        ("src/bad.c", "    return a;", "    return a + 1;"),
    ]
    for rel, old, new in cases:
        out = run(event(repo, "Edit", rel, old_string=old, new_string=new))
        before = (repo / rel).read_text()
        patch = claude.make_patch(rel, before, before.replace(old, new, 1))
        diffs = diff.load_patch(repo, patch)
        conn, _ = fresh_index(repo, want=diff.wanted(diffs, "patch"))
        try:
            res = check.run(conn, repo, diffs, mode="patch", outliner=Outliner(repo))
            v = check.verdict(check.to_contract(conn, res, "t")).verdict
        finally:
            conn.close()
        if out is None:
            assert v == "clear", (rel, old, v)


@pytest.mark.parametrize("rel", ["Makefile", "CMakeLists.txt", "build/meson.build"])
def test_a_build_file_edit_prints_one_line(repo, rel):
    (repo / rel).parent.mkdir(exist_ok=True)
    (repo / rel).write_text("CC=cc\n")
    out = run(event(repo, "Edit", rel, old_string="CC=cc", new_string="CC=gcc"))
    assert out is not None
    (line,) = lines(out)
    assert "could not check" in line and "a build file changed" in line and rel in line
