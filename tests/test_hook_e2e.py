"""End to end: a real headless Claude Code session edits a C function in a breaking way, and the
AeroTrace warning reaches the session. Costs real money (well under $1), so it is skipped by default:

    uv run pytest -m e2e -s tests/test_hook_e2e.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from aerotrace.index.build import build_index
from aerotrace.mcp import install

pytestmark = pytest.mark.e2e

PROMPT = (
    "In src/calc.c, change the function add so it takes a third parameter `int c` and returns a + b + c. "
    "Use the Edit tool once, on src/calc.c only. Do not edit any other file. Then stop."
)


@pytest.mark.skipif(shutil.which("claude") is None, reason="Claude Code is not installed")
def test_claude_session_sees_the_warning(repo_factory, monkeypatch, tmp_path):
    root = repo_factory("testimpact").resolve()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    build_index(root, jobs=1)
    monkeypatch.setattr(install, "launch_command", lambda: [sys.executable, "-m", "aerotrace", "mcp"])
    install.apply(install.hook_plan(root))

    r = subprocess.run(
        ["claude", "-p", PROMPT, "--output-format", "stream-json", "--verbose", "--model", "haiku",
         "--max-turns", "4", "--max-budget-usd", "1", "--permission-mode", "acceptEdits"],
        cwd=root, capture_output=True, text=True, timeout=300,
    )  # fmt: skip
    (tmp_path / "stream.jsonl").write_text(r.stdout)
    print(f"\nstream-json: {tmp_path / 'stream.jsonl'}")
    events = [json.loads(line) for line in r.stdout.splitlines() if line.startswith("{")]
    assert events, r.stderr[-2000:]
    result = events[-1]
    print(f"result: {result.get('subtype')}, cost ${result.get('total_cost_usd')}")
    # stream-json does not repeat a PreToolUse hook's additionalContext; the session transcript has it,
    # as the text the model was given ("PreToolUse:Edit hook additional context: ...").
    sid = events[0]["session_id"]
    transcripts = sorted(Path.home().glob(f".claude/projects/*/{sid}.jsonl"))
    assert transcripts, f"no transcript for session {sid}"
    hits = []
    for line in transcripts[0].read_text().splitlines():
        e = json.loads(line)
        if e.get("type") == "attachment":
            hits += [
                c["content"]
                for c in e.get("rendered") or []
                if "AeroTrace: this edit breaks code" in c.get("content", "")
            ]
    print(f"transcript: {transcripts[0]}")
    for h in hits[:1]:
        print(h)
    assert hits, "the AeroTrace warning did not reach the session"
    assert "PreToolUse:Edit hook additional context: AeroTrace: this edit breaks code" in hits[0]
    assert "twice (src/calc.c:" in hits[0] and "test_add (tests/test_unity.c:4)" in hits[0]
