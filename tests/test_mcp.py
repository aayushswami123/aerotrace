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
    assert sorted(t.name for t in tools.tools) == [
        "callees",
        "callers",
        "check",
        "data_users",
        "diff_impact",
        "find",
        "impact",
        "index_status",
        "signature_impact",
        "tests_for_change",
    ]


@pytest.mark.anyio
async def test_tests_for_change_tool(repo_factory):
    root = repo_factory("testimpact")
    async with Client(create_server(root)) as client:
        out = text_of(await client.call_tool("tests_for_change", {"symbol": "add"}))
        unknown = text_of(await client.call_tool("tests_for_change", {"symbol": "divide"}))
        outside = text_of(await client.call_tool("tests_for_change", {}))
    assert "exact     Calc.Adds" in out
    assert "Not known which tests reach this code (1):" in unknown
    assert "This does not mean no test runs this code" in unknown
    assert "Could not read the change" in outside  # not a git repo: says so, runs nothing


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


@pytest.mark.anyio
async def test_diff_impact_sees_uncommitted_edits_and_patches(repo_factory, monkeypatch):
    import subprocess

    root = repo_factory("c_multi")
    for k in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"):
        monkeypatch.setenv(k, "2026-01-01T00:00:00Z")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    for args in (
        ["init", "-q"],
        ["add", "-A"],
        ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"],
    ):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    async with Client(create_server(root)) as client:
        clean = text_of(await client.call_tool("diff_impact", {}))
        patch = "--- a/prog_a/util.c\n+++ b/prog_a/util.c\n@@ -4 +4 @@\n-    count++;\n+    count += 5;\n"
        planned = text_of(await client.call_tool("diff_impact", {"patch": patch}))
        shared = root / "lib" / "shared.c"
        shared.write_text(shared.read_text().replace("g_total += x;", "g_total += 2 * x;"))
        edited = text_of(await client.call_tool("diff_impact", {}))
        bad = text_of(await client.call_tool("diff_impact", {"base": "--output=/tmp/x"}))
    assert "0 functions changed" in clean
    assert "bump  prog_a/util.c:3" in planned and "util_run" in planned
    assert edited.startswith("(Re-indexed 1 changed file(s): lib/shared.c.)")
    assert "add  lib/shared.c:5" in edited and "[data] via add" in edited
    assert bad.startswith("Could not read the change:")


@pytest.mark.anyio
async def test_status_explains_flag_problems(repo_factory):
    root = repo_factory("c_multi")
    (root / "lib" / "extra.c").write_text("int extra(void) { return 1; }\n")  # not in compile_commands.json
    async with Client(create_server(root)) as client:
        status = text_of(await client.call_tool("index_status", {}))
    assert "Note: 1 C/C++ source file is not in compile_commands.json" in status and "lib/extra.c" in status


@pytest.mark.anyio
async def test_signature_impact_lists_call_sites_and_return_value_uses(repo_factory):
    async with Client(create_server(repo_factory("c_data"))) as client:
        out = text_of(await client.call_tool("signature_impact", {"symbol": "parse_num"}))
    assert "affects 12 call sites in 2 functions" in out
    assert "uses  src/use.c:10  result compared with ==" in out
    assert "DOES NOT COMPILE NOW: passes 1 argument, it takes 2" in out
    assert "src/parse.c:6  in the initializer of parsers" in out


@pytest.mark.anyio
async def test_signature_impact_is_short_by_default_and_can_show_all(repo_factory):
    async with Client(create_server(repo_factory("c_rank"))) as client:
        short = text_of(await client.call_tool("signature_impact", {"symbol": "f@src/a_first.c"}))
        full = text_of(
            await client.call_tool("signature_impact", {"symbol": "f@src/a_first.c", "show_all": True})
        )
        zero = await client.call_tool("signature_impact", {"symbol": "f@src/a_first.c", "limit": 0})
        both = await client.call_tool(
            "signature_impact", {"symbol": "f@src/a_first.c", "limit": 3, "show_all": True}
        )
    assert "  ... and 12 more call sites (call again with show_all=true, or a bigger limit)." in short
    assert "more call sites" not in full and full.count("  - many  src/a_first.c:") == 20
    assert zero.is_error and "limit must be 1 or more." in text_of(zero)
    assert both.is_error and "not both" in text_of(both)


@pytest.mark.anyio
async def test_data_users_tool(repo_factory):
    async with Client(create_server(repo_factory("c_fields"))) as client:
        out = text_of(await client.call_tool("data_users", {"name": "conn::count", "limit": 2}))
        many = text_of(await client.call_tool("data_users", {"name": "count"}))
        value = text_of(await client.call_tool("data_users", {"name": "conn::count", "from_function": "put"}))
    assert "Summary: 7 functions use it in 1 file: 5 write it, 2 only read it (7 exact)." in out
    assert "... and 5 more functions (call again with show_all=true, or a bigger limit)." in out
    assert "Limits of this answer:" in out
    assert "matches 5 variables" in many
    assert "[probable]" in value and "maybe of another object" in value
