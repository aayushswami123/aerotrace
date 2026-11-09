"""The MCP server while the index is being built: progress, the early answer, failures, shutting down."""

import errno
import inspect
import json
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import anyio
import pytest
from mcp import Client

from aerotrace.index.build import (
    LibclangLoadError,
    NoSourcesError,
    ParserCrashed,
    Progress,
    build_index,
)
from aerotrace.mcp.server import busy_text, create_server, failure_text
from conftest import GOLDEN


@pytest.fixture
def anyio_backend():
    return "asyncio"


def text_of(result) -> str:
    return "\n".join(c.text for c in result.content if getattr(c, "text", None))


class SlowBuild:
    """A build that reports "3 of 10 files done", then waits for `release`, then (unless told to fail)
    runs the real build. `calls` counts how often it was started."""

    def __init__(self, fail: BaseException | None = None, before=None, after=None):
        self.release = threading.Event()
        self.calls = 0
        self.fail = fail
        self.before = before
        self.after = after

    def __call__(self, root, **kw):
        self.calls += 1
        if self.before:
            self.before(root)
        kw["progress"](Progress("parse", 3, 10, 1))
        assert self.release.wait(30), "the test never released the build"
        if self.fail is not None:
            raise self.fail
        report = build_index(root, **{**kw, "isolate": False, "jobs": 1})
        if self.after:
            self.after(root)
        return report


EARLY = (
    "AeroTrace is still building the index (no index yet): 3 of 10 files done. This may take a few minutes "
    "on a large repo. No answer yet: ask again shortly, or call `index_status` to see progress."
)
STATUS = (
    "AeroTrace is still building the index (no index yet): 3 of 10 files done. This may take a few minutes "
    "on a large repo. Ask again shortly."
)


async def until_answered(client, tool, args, tries=100):
    """Call until the answer is not "still building" (the build was released)."""
    for _ in range(tries):
        out = text_of(await client.call_tool(tool, args))
        if not out.startswith("AeroTrace is still building"):
            return out
        await anyio.sleep(0.1)
    raise AssertionError("the build never finished")


# ---------------------------------------------------------------- the early answer


@pytest.mark.anyio
async def test_long_build_answers_early_then_answers_for_real(repo_factory):
    root = repo_factory("c_multi")
    slow = SlowBuild()
    async with Client(create_server(root, wait=0.3, build=slow)) as client:
        t = time.monotonic()
        assert text_of(await client.call_tool("impact", {"symbol": "add"})) == EARLY
        assert time.monotonic() - t < 3
        assert text_of(await client.call_tool("callers", {"symbol": "add"})) == EARLY  # asked again
        t = time.monotonic()
        assert text_of(await client.call_tool("index_status", {})) == STATUS
        assert time.monotonic() - t < 0.25  # does not wait while a build runs

        outs = []

        async def ask():
            outs.append(text_of(await client.call_tool("find", {"query": "add"})))

        async with anyio.create_task_group() as tg:
            for _ in range(5):
                tg.start_soon(ask)
        assert outs == [EARLY] * 5
        assert slow.calls == 1  # never two builds

        slow.release.set()
        out = await until_answered(client, "impact", {"symbol": "add"})
    assert out.startswith("(Built the index:")  # shown once, even though every waiting call timed out
    assert "get reads g_total" in out
    assert slow.calls == 1


@pytest.mark.anyio
async def test_progress_is_sent_while_waiting(repo_factory):
    root = repo_factory("c_multi")
    slow = SlowBuild()
    seen = []

    async def on_progress(progress, total, message):
        seen.append((progress, total, message))
        if message.startswith("Parsing"):
            slow.release.set()

    async with Client(create_server(root, wait=20, build=slow)) as client:
        out = text_of(await client.call_tool("impact", {"symbol": "add"}, progress_callback=on_progress))
    assert (3, 10, "Parsing: 3 of 10 files done (1 reused from cache).") in seen
    assert out.startswith("(Built the index:")


@pytest.mark.anyio
async def test_real_build_sends_progress_in_order(repo_factory, monkeypatch):
    import aerotrace.mcp.server as srv

    monkeypatch.setattr(srv, "TICK_SECONDS", 0.01)
    root = repo_factory("c_multi")
    seen = []

    async def on_progress(progress, total, message):
        seen.append((progress, total, message))

    async with Client(create_server(root, jobs=1)) as client:
        out = text_of(await client.call_tool("find", {"query": "add"}, progress_callback=on_progress))
    assert out.startswith("(Built the index:")
    assert seen, "no progress was sent"
    assert [p for p, _, _ in seen] == sorted(p for p, _, _ in seen)
    for _, total, message in seen:
        assert message.startswith(("Finding the source", "Checking which", "Parsing: ", "Saving the index"))
        assert total in (None, 6)  # c_multi has 6 source files


@pytest.mark.anyio
async def test_finished_build_that_misses_new_edits_is_followed_by_another(repo_factory):
    root = repo_factory("c_multi")

    def edit_during_build(root):  # the build already read the file, then the user saves an edit
        if slow.calls == 1:
            shared = root / "lib" / "shared.c"
            shared.write_text(shared.read_text() + "\nvoid twice(void) {\n    add(2);\n    add(2);\n}\n")

    slow = SlowBuild(after=edit_during_build)
    slow.release.set()
    async with Client(create_server(root, build=slow)) as client:
        out = text_of(await client.call_tool("callers", {"symbol": "add", "depth": 1}))
    assert slow.calls == 2
    assert "twice" in out


@pytest.mark.anyio
async def test_cancelled_call_does_not_stop_the_build(repo_factory):
    root = repo_factory("c_multi")
    slow = SlowBuild()
    async with Client(create_server(root, wait=10, build=slow)) as client:
        with anyio.move_on_after(0.3):
            await client.call_tool("impact", {"symbol": "add"})
        slow.release.set()
        out = await until_answered(client, "impact", {"symbol": "add"})
    assert "get reads g_total" in out
    assert slow.calls == 1


@pytest.mark.anyio
async def test_note_of_a_finished_build_is_kept_when_the_next_build_runs_long(repo_factory):
    root = repo_factory("c_multi")
    second = threading.Event()
    calls = []

    def build(root, **kw):
        calls.append(1)
        if len(calls) == 2:  # the rebuild for the edit below runs long
            kw["progress"](Progress("parse", 0, 6, 5))
            assert second.wait(30)
        report = build_index(root, **{**kw, "isolate": False, "jobs": 1})
        if len(calls) == 1:  # the user saves an edit while the first build runs
            shared = root / "lib" / "shared.c"
            shared.write_text(shared.read_text() + "\nvoid twice(void) {\n    add(2);\n}\n")
        return report

    async with Client(create_server(root, wait=1.0, build=build)) as client:
        out = text_of(await client.call_tool("impact", {"symbol": "add"}))
        second.set()
    assert out.startswith("(Built the index: ")
    assert (
        "\nAeroTrace is still building the index (1 changed file(s): lib/shared.c): 0 of 6 files done." in out
    )


# ---------------------------------------------------------------- never a stale or half-written index


@pytest.mark.anyio
async def test_old_index_and_temp_files_are_never_served_while_rebuilding(repo_factory):
    root = repo_factory("c_multi")
    build_index(root, jobs=1)
    shared = root / "lib" / "shared.c"
    shared.write_text(shared.read_text() + "\nvoid twice(void) {\n    add(2);\n}\n")

    def junk_temp_file(root):  # what a build that is half-way through writing leaves
        (root / ".aerotrace" / "index.db.tmp99999").write_bytes(b"half an index")

    slow = SlowBuild(before=junk_temp_file)
    async with Client(create_server(root, wait=0.2, build=slow)) as client:
        out = text_of(await client.call_tool("callers", {"symbol": "add", "depth": 1}))
        assert out.startswith(
            "AeroTrace is still building the index (1 changed file(s): lib/shared.c): 3 of 10 files done."
        )
        slow.release.set()
        out = await until_answered(client, "callers", {"symbol": "add", "depth": 1})
    assert out.startswith("(Re-indexed 1 changed file(s): lib/shared.c.)")
    assert "twice" in out


# ---------------------------------------------------------------- failures


@pytest.mark.anyio
async def test_build_failure_reaches_every_waiting_call(repo_factory):
    root = repo_factory("c_multi")
    slow = SlowBuild(fail=RuntimeError("boom"))
    results = []

    async def ask():
        results.append(await client.call_tool("impact", {"symbol": "add"}))

    async with Client(create_server(root, wait=10, build=slow)) as client:
        async with anyio.create_task_group() as tg:
            tg.start_soon(ask)
            tg.start_soon(ask)
            await anyio.sleep(0.3)
            slow.release.set()
        assert slow.calls == 1
        want = (
            "Error executing tool impact: Could not build the index: unexpected error (RuntimeError: boom). "
            "The full error is in the MCP server log (stderr). Please report it as a bug."
        )
        assert [(r.is_error, text_of(r)) for r in results] == [(True, want)] * 2

        slow.fail = None  # the next call tries again (and now it works)
        out = text_of(await client.call_tool("impact", {"symbol": "add"}))
    assert slow.calls == 2
    assert out.startswith("(Built the index:")


def test_failure_texts(tmp_path):
    root = tmp_path / "repo"
    where = root / ".aerotrace"
    cases = [
        (
            NoSourcesError("x"),
            f"no C or C++ source files found under {root}. "
            "Start the AeroTrace MCP server in the project folder, or pass --root.",
        ),
        (
            LibclangLoadError("dlopen(libclang.dylib) failed"),
            "libclang could not be loaded (dlopen(libclang.dylib) failed). "
            "Install LLVM, or set AEROTRACE_LIBCLANG to the libclang library file.",
        ),
        (
            ParserCrashed([f"src/{c}.c" for c in "abcdefg"]),
            "a parser process crashed or ran out of memory (7 files not parsed yet, the one it crashed on "
            "among them: src/a.c, src/b.c, src/c.c, src/d.c, src/e.c, ...). Files parsed so far are cached. "
            'Ask again to retry; if it fails again, run "aerotrace index --jobs 1" in a terminal and report '
            "the bug.",
        ),
        (
            OSError(errno.ENOSPC, "No space left on device"),
            f"the disk is full (writing {where}). Free some space and ask again.",
        ),
        (
            sqlite3.OperationalError("database or disk is full"),
            f"the disk is full (writing {where}). Free some space and ask again.",
        ),
        (
            PermissionError(errno.EACCES, "Permission denied", str(where / "index.db")),
            f"no permission to write {where / 'index.db'}. AeroTrace keeps its index in {where}.",
        ),
        (MemoryError(), 'out of memory. Try fewer parsers: "aerotrace mcp --jobs 2".'),
    ]
    for exc, why in cases:
        assert failure_text(exc, root) == f"Could not build the index: {why}"


@pytest.mark.anyio
async def test_real_setup_problems_are_explained(repo_factory, tmp_path, monkeypatch):
    root = repo_factory("c_multi")
    empty = tmp_path / "build"
    empty.mkdir()
    async with Client(create_server(root, build_dir=empty)) as client:
        r = await client.call_tool("impact", {"symbol": "add"})
    assert r.is_error
    assert text_of(r) == (
        f"Error executing tool impact: Could not build the index: no compile_commands.json in {empty}. "
        f"Fix --build-dir, or create it (CMake: cmake -B {empty} -DCMAKE_EXPORT_COMPILE_COMMANDS=ON)."
    )

    nothing = tmp_path / "docs-only"
    nothing.mkdir()
    (nothing / "README.md").write_text("no code here\n")
    async with Client(create_server(nothing)) as client:
        r = await client.call_tool("index_status", {})
    assert r.is_error and "no C or C++ source files found under" in text_of(r)

    monkeypatch.setenv("AEROTRACE_LIBCLANG", str(tmp_path / "missing" / "libclang.so"))
    async with Client(create_server(repo_factory("c_data"))) as client:
        r = await client.call_tool("find", {"query": "x"})
    assert r.is_error
    assert text_of(r) == (
        "Error executing tool find: Could not build the index: AEROTRACE_LIBCLANG points to "
        f"{tmp_path / 'missing' / 'libclang.so'}, which does not exist. Fix or unset it."
    )


def test_busy_text_phases():
    assert busy_text("no index yet", Progress("flags", 0, 0, 0), status=True) == (
        "AeroTrace is still building the index (no index yet): finding the source files. "
        "This may take a few minutes on a large repo. Ask again shortly."
    )
    assert "(no index yet): checking which files changed." in busy_text(
        "no index yet", Progress("cache", 5, 2000, 5)
    )
    assert "(no index yet): all 2,000 files done, saving." in busy_text(
        "no index yet", Progress("save", 2000, 2000, 5)
    )
    assert "(no index yet): 40 of 2,000 files done." in busy_text(
        "no index yet", Progress("parse", 40, 2000, 5)
    )


# ---------------------------------------------------------------- the tool list did not change


@pytest.mark.anyio
async def test_tool_schemas_are_unchanged(repo_factory):
    async with Client(create_server(repo_factory("c_multi"))) as client:
        tools = (await client.list_tools()).tools
    got = {t.name: {"description": t.description, "inputSchema": t.input_schema} for t in tools}
    want = json.loads((GOLDEN / "mcp_tool_schemas.json").read_text())

    def same_text(tools: dict) -> dict:
        # Python 3.13 strips docstring indentation when compiling, 3.11 and 3.12 do not; cleandoc gives
        # both the same text. Input schemas are compared exactly.
        return {n: {**t, "description": inspect.cleandoc(t["description"])} for n, t in tools.items()}

    assert same_text(got) == same_text(want)  # nothing moved
    for name, t in got.items():
        assert "ctx" not in t["inputSchema"].get("properties", {}), name


# ---------------------------------------------------------------- shutting down mid-build


def _slow_repo(root: Path) -> None:
    """Four big files: parsing them takes several seconds, long enough to quit in the middle."""
    root.mkdir()
    for i in range(4):
        body = [
            f"int g{i}_{k};\nint f{i}_{k}(int x) {{ g{i}_{k} += x; return f{i}_{max(k - 1, 0)}(x) + 1; }}"
            for k in range(15000)
        ]
        (root / f"f{i}.c").write_text("\n".join(body) + "\n")


def _children(pid: int) -> set[int]:
    out = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True).stdout
    return {int(p) for p in out.split()}


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # a zombie child of ours does not count; it is gone once reaped
    return subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True
    ).stdout.strip()[:1] not in ("", "Z")


@pytest.mark.skipif(sys.platform == "win32", reason="uses pgrep")
@pytest.mark.parametrize("how", ["close stdin", "SIGTERM", "Ctrl-C"])
def test_server_exits_promptly_when_the_client_quits_mid_build(tmp_path, how):
    root = tmp_path / "slow"
    _slow_repo(root)
    proc = subprocess.Popen(
        [sys.executable, "-m", "aerotrace.cli", "mcp", "--root", str(root), "--jobs", "2"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,  # its own process group, like a server started from a terminal
    )

    def send(msg: dict) -> None:
        proc.stdin.write((json.dumps(msg) + "\n").encode())
        proc.stdin.flush()

    try:
        send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            }
        )
        assert b'"id":1' in proc.stdout.readline().replace(b" ", b"")
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "index_status", "arguments": {}},
            }
        )
        workers: set[int] = set()
        for _ in range(200):  # wait until both parsers are running
            workers = {p for p in _children(proc.pid) if "spawn_main" in _cmdline(p)}
            if len(workers) >= 2:
                break
            time.sleep(0.05)
        assert len(workers) >= 2, "the parsers never started"
        time.sleep(1)  # let them finish starting up and begin parsing (each file takes seconds)
        t = time.monotonic()
        if how == "SIGTERM":
            proc.terminate()
        elif how == "Ctrl-C":  # a terminal sends SIGINT to the whole group, parsers included
            os.killpg(proc.pid, signal.SIGINT)
        else:
            proc.stdin.close()  # the client quits
        proc.wait(timeout=15)
        took = time.monotonic() - t
    finally:
        if proc.poll() is None:
            proc.kill()
    assert took < 3, f"took {took:.1f} s to exit"
    err = proc.stderr.read()
    assert b"KeyboardInterrupt" not in err  # the parsers ignore Ctrl-C
    # (a "leaked semaphore" warning from multiprocessing's tracker can still show now and then: harmless,
    # the tracker removes them)
    time.sleep(0.5)
    assert not [p for p in workers if _alive(p)], "parser processes were left running"
    assert not (root / ".aerotrace" / "index.db").exists()  # nothing half-done was written


def _cmdline(pid: int) -> str:
    return subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout
