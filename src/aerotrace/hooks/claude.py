"""The Claude Code `PreToolUse` hook: check a proposed Edit / MultiEdit / Write before it is written.

Thin layer: it turns the hook JSON into a unified patch and asks `analysis.check` and
`analysis.rules`. It warns by default, blocks only when `.aerotrace/rules.toml` says so, and never
blocks because AeroTrace itself failed. See docs/plans/hook.md.
"""

from __future__ import annotations

import difflib
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

from aerotrace.analysis import rules as rules_mod

TOOLS = ("Edit", "MultiEdit", "Write")
OFF_FILE = Path(".aerotrace") / "hook-off"
SMALL_REFRESH = 20  # files the hook may re-index itself


class Skip(Exception):
    """The edit can't be checked; the message says why (it never blocks)."""


def couldnt(reason: str) -> str:
    return f"AeroTrace couldn't check this edit ({reason})."


def warn(text: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": text}}


def deny(text: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": text,
        }
    }


def find_root(cwd: str) -> Path:
    try:
        r = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=5
        )
        if r.returncode == 0 and r.stdout.strip():
            return Path(r.stdout.strip()).resolve()
    except (OSError, subprocess.SubprocessError):
        pass
    return Path(cwd).resolve()


def _replace(text: str, old: str, new: str, every: bool) -> str | None:
    """Claude Code's rule: `old` must be found, and only once unless `every`. None = the edit will fail."""
    if not isinstance(old, str) or not isinstance(new, str) or old == "":
        return None
    n = text.count(old)
    if n == 0 or (n > 1 and not every):
        return None
    return text.replace(old, new) if every else text.replace(old, new, 1)


def new_text(tool: str, inp: dict, old: str) -> str | None:
    """The file's text after the edit, or None when the tool call would fail anyway."""
    if tool == "Write":
        c = inp.get("content")
        return c if isinstance(c, str) else None
    if tool == "Edit":
        return _replace(old, inp.get("old_string"), inp.get("new_string"), bool(inp.get("replace_all")))
    edits = inp.get("edits")
    if not isinstance(edits, list):
        return None
    text = old
    for e in edits:
        if not isinstance(e, dict):
            return None
        text = _replace(text, e.get("old_string"), e.get("new_string"), bool(e.get("replace_all")))
        if text is None:
            return None
    return text


def make_patch(rel: str, before: str | None, after: str) -> str:
    a = before.splitlines(keepends=True) if before else []
    b = after.splitlines(keepends=True)
    if a and not a[-1].endswith("\n"):
        a[-1] += "\n\\ No newline at end of file\n"
    if b and not b[-1].endswith("\n"):
        b[-1] += "\n\\ No newline at end of file\n"
    old_name = f"a/{rel}" if before is not None else "/dev/null"
    return "".join(difflib.unified_diff(a, b, old_name, f"b/{rel}"))


def open_index(root: Path, want: list[str]):
    """Open the index, re-indexing a few changed files first. Never builds a whole index."""
    from aerotrace.index import store
    from aerotrace.index.build import SetupError, build_index, index_plan

    try:
        plan = index_plan(root, want=want)
    except SetupError as e:
        raise Skip(f"the index can't be refreshed: {e}") from e
    if plan is None:
        return store.connect(root), ""
    if plan.kind == "new":
        raise Skip("no index yet; run `aerotrace index`")
    if plan.kind == "version":
        raise Skip("the index is from another AeroTrace version or damaged; run `aerotrace index`")
    if plan.kind == "flags":
        raise Skip("the compile flags changed; run `aerotrace index`")
    n = len(set(plan.changed) | set(plan.missing))
    if n > SMALL_REFRESH:
        raise Skip(f"index is stale ({n} files changed); run `aerotrace index`")
    build_index(root, build_dir=plan.build_dir, excludes=plan.excludes)
    return store.connect(root), ""


def _tests(conn, root: Path, res) -> tuple[list[str] | None, str]:
    from aerotrace.analysis import testimpact

    ti = testimpact.for_diff(conn, root, res.diff)
    if not sum(ti.entries.values()):
        return None, "no test entry points in the index, so tests are not known."
    names = sorted({f"{t.test} ({t.file}, {t.level})" for t in ti.tests})
    if not names:
        return None, "no indexed test reaches this change."
    return names, ""


def check_edit(root: Path, rel: str, before: str | None, after: str, rules: rules_mod.Rules) -> dict | None:
    """The hook's answer for one edit of a C/C++ file. Raises Skip when it can't check."""
    from aerotrace.analysis import check
    from aerotrace.index.history import Outliner

    text = make_patch(rel, before, after)
    if not text:
        return None
    outliner = Outliner(root, None)
    change = check.load_change(root, outliner, patch_text=text)
    conn, _ = open_index(root, change.want)
    try:
        res = check.run(conn, root, change.diffs, mode="patch", outliner=outliner, old=change.old)
        contract = check.to_contract(conn, res, check._tool())
        if contract["status"] != "ok":
            lvl = "low"
            body = ""
        else:
            lvl = rules_mod.level(contract, rules)
            body = ""
            if lvl == "high":
                tests, note = _tests(conn, root, res)
                body = rules_mod.full_text(contract, tests, note)
            elif lvl == "medium":
                body = rules_mod.short_text(contract)
    finally:
        conn.close()
    stop = rules_mod.blocking(rules, rel, lvl)
    if stop:
        if not body:
            body = (
                rules_mod.full_text(contract, None)
                if contract["status"] == "ok"
                else "AeroTrace: protected file."
            )
        return deny(rules_mod.blocked_text(body, stop))
    return warn(body) if body else None


def _join(*parts: str) -> str:
    return "\n".join(p for p in parts if p)


def _cwd(event: dict) -> str:
    return event["cwd"] if isinstance(event.get("cwd"), str) else os.getcwd()


def handle(event: dict, root: Path, rules: rules_mod.Rules, rules_warning: str) -> dict | None:
    """The JSON to print for one hook event (None = print nothing). Raises on internal errors."""
    from aerotrace.analysis.diff import CODE_EXTS

    tool = event.get("tool_name")
    inp = event.get("tool_input")
    if tool not in TOOLS or not isinstance(inp, dict) or not isinstance(inp.get("file_path"), str):
        return None
    cwd = _cwd(event)
    path = Path(inp["file_path"])
    path = (Path(cwd) / path if not path.is_absolute() else path).resolve()
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        return None
    if (root / OFF_FILE).exists() or rel.startswith(".aerotrace/"):
        return None
    try:
        before = path.read_text(encoding="utf-8") if path.is_file() else None
    except (OSError, UnicodeDecodeError):
        return warn(_join(rules_warning, couldnt("the file could not be read")))
    after = new_text(tool, inp, before or "")
    if after is None or after == before:
        return warn(rules_warning) if rules_warning else None
    if path.suffix.lower() not in CODE_EXTS:
        stop = rules_mod.blocking(rules, rel, "low")
        if stop:
            return deny(rules_mod.blocked_text(f"AeroTrace: {rel} is protected.", stop))
        return warn(rules_warning) if rules_warning else None
    out = check_edit(root, rel, before, after, rules)
    if rules_warning:
        if out is None:
            return warn(rules_warning)
        h = out["hookSpecificOutput"]
        key = "permissionDecisionReason" if "permissionDecisionReason" in h else "additionalContext"
        h[key] = _join(rules_warning, h[key])
    return out


def answer(event: dict, timeout: float | None = None) -> dict | None:
    """`handle` with a deadline and every failure turned into allow + a short note."""
    box: dict = {}
    try:
        root = find_root(_cwd(event))
        rules, rules_warning = rules_mod.load_rules(root)
    except Exception as e:
        return warn(couldnt(f"internal error: {type(e).__name__}: {e}"))
    if timeout is None:
        timeout = rules.timeout_seconds

    def work() -> None:
        try:
            box["out"] = handle(event, root, rules, rules_warning)
        except Skip as e:
            box["out"] = warn(couldnt(str(e)))
        except Exception as e:  # never block on our own failure
            box["out"] = warn(couldnt(f"internal error: {type(e).__name__}: {e}"))

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return warn(couldnt(f"it took longer than {timeout:g} s"))
    return box.get("out")


def main(stdin=None, stdout=None) -> int:
    """Read one hook event, print the answer. Always exits 0 (an error never blocks the edit)."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    try:
        event = json.loads(stdin.read() or "{}")
    except ValueError:
        return 0
    if not isinstance(event, dict):
        return 0
    out = answer(event)
    if out is not None:
        stdout.write(json.dumps(out, sort_keys=True) + "\n")
    stdout.flush()
    return 0
