"""`aerotrace install --client claude|cursor|codex`: add AeroTrace to an agent's MCP settings.

We only ever add or replace our own `aerotrace` entry. Other servers and settings are kept as they are,
and a file we can't read safely is left alone with an error.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

SERVER_NAME = "aerotrace"
CLIENTS = ("claude", "cursor", "codex")
_EPHEMERAL_MARKERS = ("/.cache/uv/", "/uv/archive-", "/uv/builds-", "/tmp/", "/var/folders/", "\\Temp\\")


class InstallError(Exception):
    pass


@dataclass
class Plan:
    client: str
    path: Path  # the settings file we change
    content: str  # its full new content
    note: str  # what to do next, in plain words


def launch_command() -> list[str]:
    """How the agent should start our server: [command, *args]. Must survive after this process exits."""
    exe = shutil.which("aerotrace")
    candidates = [exe] if exe else []
    candidates.append(str(Path(sys.executable).parent / "aerotrace"))
    for c in candidates:
        if c and Path(c).is_file() and not any(m in c for m in _EPHEMERAL_MARKERS):
            return [str(Path(c).resolve()), "mcp"]
    if not any(m in sys.executable for m in _EPHEMERAL_MARKERS):
        return [sys.executable, "-m", "aerotrace", "mcp"]
    raise InstallError(
        "AeroTrace is running from a temporary folder (for example through `uvx`), so an agent could not start "
        "it later. Install it first, then run this again:\n"
        "  uv tool install git+https://github.com/aayushswami123/aerotrace\n"
        "  aerotrace install --client <claude|cursor|codex>"
    )


def _merge_json(path: Path, entry: dict) -> str:
    data: dict = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8") or "{}")
        except ValueError as e:
            raise InstallError(
                f"{path} is not valid JSON ({e}); fix it or remove it, then run this again"
            ) from e
        if not isinstance(data, dict) or not isinstance(data.get("mcpServers", {}), dict):
            raise InstallError(f"{path} does not look like an MCP settings file; leaving it alone")
    servers = data.setdefault("mcpServers", {})
    servers[SERVER_NAME] = entry
    return json.dumps(data, indent=2) + "\n"


def _toml_str(s: str) -> str:
    return json.dumps(s)  # a JSON string is a valid TOML basic string


def _merge_toml(path: Path, command: list[str]) -> str:
    """Replace our [mcp_servers.aerotrace] table in a Codex config.toml, keeping everything else."""
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise InstallError(f"{path} is not valid TOML ({e}); fix it, then run this again") from e
    ours = (f"[mcp_servers.{SERVER_NAME}]", f"[mcp_servers.{SERVER_NAME}.")
    kept: list[str] = []
    skipping = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            skipping = stripped == ours[0] or stripped.startswith(ours[1])
        if not skipping:
            kept.append(line)
    while kept and not kept[-1].strip():
        kept.pop()
    block = [
        f"[mcp_servers.{SERVER_NAME}]",
        f"command = {_toml_str(command[0])}",
        f"args = [{', '.join(_toml_str(a) for a in command[1:])}]",
    ]
    new = "\n".join([*kept, *([""] if kept else []), *block]) + "\n"
    parsed = tomllib.loads(new)  # never write a file we can't read back
    if parsed["mcp_servers"][SERVER_NAME]["command"] != command[0]:
        raise InstallError(f"could not update {path} safely")
    return new


def plan(client: str, root: Path, home: Path | None = None) -> Plan:
    """Work out what to write, without writing anything."""
    home = home or Path.home()
    command = launch_command()
    if client == "claude":
        path = root / ".mcp.json"
        entry = {"type": "stdio", "command": command[0], "args": command[1:]}
        note = "Restart Claude Code in this folder and approve the 'aerotrace' server when asked."
        return Plan(client, path, _merge_json(path, entry), note)
    if client == "cursor":
        path = root / ".cursor" / "mcp.json"
        entry = {
            "type": "stdio",
            "command": command[0],
            "args": [*command[1:], "--root", "${workspaceFolder}"],
        }
        note = "Open this folder in Cursor, then turn on 'aerotrace' under Settings > MCP."
        return Plan(client, path, _merge_json(path, entry), note)
    if client == "codex":
        codex_home = os.environ.get("CODEX_HOME")
        path = (Path(codex_home) if codex_home else home / ".codex") / "config.toml"
        note = "Start Codex inside any C/C++ repo; it runs AeroTrace in that folder."
        return Plan(client, path, _merge_toml(path, command), note)
    raise InstallError(f"unknown client '{client}'; choose one of: {', '.join(CLIENTS)}")


HOOK_MATCHER = "Edit|MultiEdit|Write"
HOOK_TIMEOUT = 30  # seconds; a backstop only, the hook keeps its own 5 s budget


def _is_ours(h: object, command: str) -> bool:
    """Only our own exact command counts as ours; any other hook is the user's and is kept."""
    return isinstance(h, dict) and h.get("command") == command


def hook_plan(root: Path) -> Plan:
    """Add the PreToolUse guardrail hook to <repo>/.claude/settings.json, keeping everything else."""
    path = root / ".claude" / "settings.json"
    command = " ".join(shlex.quote(c) for c in [*launch_command()[:-1], "hook", "run"])
    data: dict = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8") or "{}")
        except ValueError as e:
            raise InstallError(
                f"{path} is not valid JSON ({e}); fix it or remove it, then run this again"
            ) from e
    hooks = data.get("hooks", {}) if isinstance(data, dict) else None
    pre = hooks.get("PreToolUse", []) if isinstance(hooks, dict) else None
    if not isinstance(pre, list) or not all(isinstance(g, dict) for g in pre):
        raise InstallError(f"{path} does not look like a Claude Code settings file; leaving it alone")
    kept = []
    for g in pre:
        inner = g.get("hooks")
        if isinstance(inner, list) and any(_is_ours(h, command) for h in inner):
            rest = [h for h in inner if not _is_ours(h, command)]
            if not rest:
                continue
            g = {**g, "hooks": rest}
        kept.append(g)
    kept.append(
        {"matcher": HOOK_MATCHER, "hooks": [{"type": "command", "command": command, "timeout": HOOK_TIMEOUT}]}
    )
    data.setdefault("hooks", {})["PreToolUse"] = kept
    note = (
        "Added the AeroTrace edit check (a PreToolUse hook): before Claude edits C/C++ code it is told what "
        "the edit may break. It warns only; `aerotrace hook off` turns it off."
    )
    return Plan("claude", path, json.dumps(data, indent=2) + "\n", note)


def apply(p: Plan) -> None:
    p.path.parent.mkdir(parents=True, exist_ok=True)
    if p.path.is_file() and p.client == "codex":
        backup = p.path.with_suffix(p.path.suffix + ".bak")
        shutil.copy2(p.path, backup)
    tmp = p.path.with_suffix(p.path.suffix + ".tmp")
    tmp.write_text(p.content, encoding="utf-8")
    tmp.replace(p.path)
