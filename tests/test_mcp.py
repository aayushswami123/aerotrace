import json
import shutil
import sys
import tomllib

import pytest
from mcp import Client

from aerotrace.mcp import install
from aerotrace.mcp.server import create_server


@pytest.fixture
def anyio_backend():
    return "asyncio"


def text_of(result) -> str:
    return "\n".join(c.text for c in result.content if getattr(c, "text", None))


# ---------------------------------------------------------------- the server


@pytest.mark.anyio
async def test_server_lists_its_tools(repo_factory):
    server = create_server(repo_factory("c_multi"))
    async with Client(server) as client:
        tools = await client.list_tools()
    assert sorted(t.name for t in tools.tools) == ["callees", "callers", "find", "impact", "index_status"]


@pytest.mark.anyio
async def test_first_question_builds_the_index(repo_factory):
    root = repo_factory("c_multi")
    async with Client(create_server(root)) as client:
        out = text_of(await client.call_tool("impact", {"symbol": "add"}))
    assert out.startswith("(Built the index:")
    assert "get reads g_total" in out
    assert (root / ".aerotrace" / "index.db").is_file()


@pytest.mark.anyio
async def test_edits_are_picked_up_before_answering(repo_factory):
    root = repo_factory("c_multi")
    async with Client(create_server(root)) as client:
        await client.call_tool("callers", {"symbol": "add"})
        shared = root / "lib" / "shared.c"
        shared.write_text(shared.read_text() + "\nvoid twice(void) {\n    add(2);\n    add(2);\n}\n")
        out = text_of(await client.call_tool("callers", {"symbol": "add", "depth": 1}))
    assert out.startswith("(Re-indexed 1 changed file(s): lib/shared.c.)")
    assert "twice" in out


@pytest.mark.anyio
async def test_ambiguous_name_asks_the_agent_to_choose(repo_factory):
    async with Client(create_server(repo_factory("c_multi"))) as client:
        out = text_of(await client.call_tool("callees", {"symbol": "main"}))
        exact = text_of(await client.call_tool("callees", {"symbol": "main@prog_a/main.c", "depth": 1}))
    assert "matches 2 functions" in out and "main@prog_b/main.c" in out
    assert "util_run" in exact and "util_run_b" not in exact


@pytest.mark.anyio
async def test_status_and_find(repo_factory):
    async with Client(create_server(repo_factory("c_multi"))) as client:
        status = text_of(await client.call_tool("index_status", {}))
        found = text_of(await client.call_tool("find", {"query": "lib/table.c:11"}))
    assert "Compile flags: compile_commands.json." in status
    assert found.startswith("dispatch@lib/table.c")


# ---------------------------------------------------------------- install


@pytest.fixture
def fixed_command(monkeypatch):
    monkeypatch.setattr(install, "launch_command", lambda: ["/opt/at/bin/aerotrace", "mcp"])


def test_claude_install_keeps_other_servers(tmp_path, fixed_command):
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
    p = install.plan("claude", tmp_path, home=tmp_path / "home")
    install.apply(p)
    data = json.loads((tmp_path / ".mcp.json").read_text())
    assert data["mcpServers"]["other"] == {"command": "x"}
    assert data["mcpServers"]["aerotrace"] == {
        "type": "stdio",
        "command": "/opt/at/bin/aerotrace",
        "args": ["mcp"],
    }


def test_cursor_install_passes_the_workspace_folder(tmp_path, fixed_command):
    install.apply(install.plan("cursor", tmp_path, home=tmp_path / "home"))
    entry = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())["mcpServers"]["aerotrace"]
    assert entry["args"] == ["mcp", "--root", "${workspaceFolder}"]


def test_codex_install_replaces_only_our_table(tmp_path, fixed_command, monkeypatch):
    monkeypatch.delenv("CODEX_HOME", raising=False)
    home = tmp_path / "home"
    cfg = home / ".codex" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        'model = "x"\n\n[mcp_servers.aerotrace]\ncommand = "old"\n\n[mcp_servers.aerotrace.env]\nA = "1"\n\n'
        '[mcp_servers.other]\ncommand = "keep"\n'
    )
    install.apply(install.plan("codex", tmp_path, home=home))
    data = tomllib.loads(cfg.read_text())
    assert data["model"] == "x"
    assert data["mcp_servers"]["other"] == {"command": "keep"}
    assert data["mcp_servers"]["aerotrace"] == {"command": "/opt/at/bin/aerotrace", "args": ["mcp"]}
    assert (cfg.parent / "config.toml.bak").read_text().count('command = "old"') == 1
    # running it twice changes nothing
    before = cfg.read_text()
    install.apply(install.plan("codex", tmp_path, home=home))
    assert cfg.read_text() == before


def test_broken_settings_file_is_left_alone(tmp_path, fixed_command):
    (tmp_path / ".mcp.json").write_text("{ not json")
    with pytest.raises(install.InstallError):
        install.plan("claude", tmp_path)
    assert (tmp_path / ".mcp.json").read_text() == "{ not json"


def test_temporary_install_is_refused(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setattr(sys, "executable", "/Users/x/.cache/uv/archive-v0/abc/bin/python")
    with pytest.raises(install.InstallError, match="uv tool install"):
        install.launch_command()


def test_codex_install_respects_codex_home(tmp_path, fixed_command, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "ch"))
    p = install.plan("codex", tmp_path, home=tmp_path / "home")
    assert p.path == tmp_path / "ch" / "config.toml"
