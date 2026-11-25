"""A partial clone must never make AeroTrace fetch or write into .git/ (product rules 2 and 6)."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from aerotrace.cli import main


def _git_version() -> tuple[int, int]:
    out = subprocess.run(["git", "--version"], capture_output=True, text=True).stdout
    m = re.search(r"(\d+)\.(\d+)", out)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


pytestmark = pytest.mark.skipif(_git_version() < (2, 45), reason="GIT_NO_LAZY_FETCH needs git 2.45 or newer")

V1 = "int get(int x) {\n    return x + 1;\n}\n"
V2 = "int get(int x) {\n    return x + 2;\n}\n"
V3 = "int get(int x) {\n    return x + 3;\n}\n"
MAIN = '#include "a.h"\nint main(void) { return get(1); }\n'


def _run(cwd: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return r.stdout


def _packs(clone: Path) -> list[str]:
    return sorted(p.name for p in (clone / ".git" / "objects" / "pack").glob("*"))


def _missing(clone: Path) -> set[str]:
    out = _run(clone, "rev-list", "--objects", "--all", "--missing=print")
    return {line[1:].strip() for line in out.splitlines() if line.startswith("?")}


def _make_clone(tmp_path: Path, filt: str) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    _run(work, "init", "-q", "-b", "main")
    (work / "a.h").write_text("int get(int x);\n")
    (work / "a.c").write_text(V1)
    (work / "main.c").write_text(MAIN)
    _run(work, "add", "-A")
    _run(work, "commit", "-q", "-m", "one")
    (work / "a.c").write_text(V2)
    cc = [
        {"directory": "/x", "file": f, "arguments": ["cc", "-c", f, "-o", f + ".o"]}
        for f in ("a.c", "main.c")
    ]
    (work / "compile_commands.json").write_text(json.dumps(cc))
    _run(work, "add", "-A")
    _run(work, "commit", "-q", "-m", "two")
    bare = tmp_path / "bare.git"
    _run(tmp_path, "clone", "-q", "--bare", str(work), str(bare))
    _run(bare, "config", "uploadpack.allowFilter", "true")
    _run(bare, "config", "uploadpack.allowAnySHA1InWant", "true")
    dest = tmp_path / "clone"
    _run(tmp_path, "clone", "-q", f"--filter={filt}", f"file://{bare}", str(dest))
    # the cloned compile_commands.json must point at the clone
    cc = [{**e, "directory": str(dest)} for e in cc]
    (dest / "compile_commands.json").write_text(json.dumps(cc))
    _run(dest, "update-index", "--assume-unchanged", "compile_commands.json")
    (dest / "a.c").write_text(V3)
    assert _missing(dest), "the repro needs a missing blob"
    return dest


@pytest.fixture
def clone(tmp_path):
    return _make_clone(tmp_path, "blob:none")


def test_tree_filter_clone_names_the_gap_too(tmp_path, capsys):
    clone = _make_clone(tmp_path, "tree:0")
    code = main(["diff", "HEAD~1", "--root", str(clone)])
    text = capsys.readouterr().err
    assert code == 2
    assert "partial clone" in text


def test_reason_skips_trace_lines():
    from aerotrace.toolchain.git import _reason

    assert _reason(["trace: run_command", "fatal: bad tree object abc", "x"]) == "fatal: bad tree object abc"
    assert _reason(["a", "b"]) == "b"


@pytest.mark.parametrize("cmd", ["diff", "check", "tests"])
def test_no_fetch_no_write_and_the_gap_is_named(clone, capsys, cmd):
    packs, missing = _packs(clone), _missing(clone)
    code = main([cmd, "HEAD~1", "--root", str(clone)])
    out = capsys.readouterr()
    assert _packs(clone) == packs, "git wrote pack files into .git/"
    assert _missing(clone) == missing, "git fetched a missing object"
    text = out.out + out.err
    assert "partial clone" in text
    assert "git fetch" in text
    assert code == 2, "a comparison that could not be made must not exit 0"


# ---- these need no partial clone and run on any git ----


def test_every_git_call_gets_the_safe_environment(monkeypatch, tmp_path):
    from aerotrace.toolchain import git

    seen: list[tuple[list[str], dict[str, str]]] = []

    def fake(cmd, **kw):
        seen.append((cmd, kw["env"]))
        return subprocess.CompletedProcess(cmd, 0, stdout=b"true\n", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake)
    git.is_repo(tmp_path)
    git.toplevel(tmp_path)
    assert len(seen) == 2
    for cmd, env in seen:
        assert cmd[0] == "git"
        assert env["GIT_NO_LAZY_FETCH"] == "1"
        assert env["GIT_OPTIONAL_LOCKS"] == "0"
        assert "maintenance.auto=false" in cmd and "gc.auto=0" in cmd


def test_only_the_git_helper_runs_git():
    src = Path(__file__).parent.parent / "src" / "aerotrace"
    for path in sorted(src.rglob("*.py")):
        if path.name == "git.py" and path.parent.name == "toolchain":
            continue
        text = path.read_text()
        assert not re.search(r"""\[\s*["']git["']""", text), f"{path} runs git on its own"
