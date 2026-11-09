"""The MCP server: a thin layer over `aerotrace.analysis`. Agents start it with `aerotrace mcp`.

It keeps the index fresh by itself: before every answer it checks whether indexed files changed, and if
so re-indexes (only changed files are re-parsed). Nothing is printed to stdout, which is the MCP wire.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

from mcp.server import MCPServer

from aerotrace import __version__
from aerotrace.analysis import graph, render, symbols
from aerotrace.index import store
from aerotrace.index.build import build_index

log = logging.getLogger("aerotrace.mcp")

INSTRUCTIONS = """\
AeroTrace knows how this C/C++ codebase fits together: who calls what, through direct calls, virtual calls
and function pointers, and which globals and struct fields each function reads and writes. It runs locally
and its answers are deterministic.

Use it BEFORE you edit C or C++ code:
- Before changing a function, call `impact` on it. Read the affected callers and the "Limits of this answer".
- Use `callers` / `callees` to follow the code, and `find` when you are not sure of a name.
- Labels: no label = a direct call; [virtual] = may land on this override; [via_pointer] = matched by
  function-pointer type; [ambiguous] = several functions share this name; [data] = reads data the changed
  function writes. Treat labelled links as real possibilities, not proof.
- If a name matches several functions, ask again with `name@path/to/file.c` or `file.c:LINE`.
"""


class IndexState:
    """Opens the index for each request, building or refreshing it first when needed."""

    def __init__(self, root: Path, build_dir: Path | None = None):
        self.root = root
        self.build_dir = build_dir
        self._lock = threading.Lock()

    def open(self) -> tuple[sqlite3.Connection, str]:
        """(connection, note for the agent about what happened to the index, or "")."""
        with self._lock:
            note = ""
            try:
                conn = store.connect(self.root)
            except FileNotFoundError:
                report = build_index(self.root, build_dir=self.build_dir, log=log.info)
                note = f"(Built the index: {report.functions} functions from {report.units} files.)"
                return store.connect(self.root), note
            changed = store.changed_files(conn, self.root)
            if changed:
                conn.close()
                build_index(self.root, build_dir=self.build_dir, log=log.info)
                shown = ", ".join(changed[:3]) + (" ..." if len(changed) > 3 else "")
                note = f"(Re-indexed {len(changed)} changed file(s): {shown}.)"
                conn = store.connect(self.root)
            return conn, note


def _with_note(text: str, note: str) -> str:
    return f"{note}\n{text}" if note else text


def create_server(root: Path, build_dir: Path | None = None) -> MCPServer:
    state = IndexState(root, build_dir)
    server = MCPServer("aerotrace", instructions=INSTRUCTIONS, version=__version__)

    def resolve(conn: sqlite3.Connection, symbol: str) -> tuple[symbols.Fn | None, str]:
        fns = symbols.find(conn, symbol)
        if len(fns) == 1:
            return fns[0], ""
        return None, render.candidates_text(symbol, fns or symbols.search(conn, symbol))

    @server.tool()
    def impact(symbol: str, depth: int = 5, limit: int = 40) -> str:
        """What could break if this C/C++ function changes: every caller up to `depth` steps away (direct,
        virtual and function-pointer calls), functions that read globals or fields it writes, and the
        limits of the answer. `symbol` is a name, ns::Class::method, name@file, or file.c:LINE."""
        conn, note = state.open()
        with closing(conn):
            fn, msg = resolve(conn, symbol)
            if fn is None:
                return _with_note(msg, note)
            return _with_note(render.impact_text(graph.impact(conn, fn, depth), limit), note)

    @server.tool()
    def callers(symbol: str, depth: int = 3, limit: int = 40) -> str:
        """Functions that call this one, directly or through other functions, up to `depth` steps."""
        conn, note = state.open()
        with closing(conn):
            fn, msg = resolve(conn, symbol)
            if fn is None:
                return _with_note(msg, note)
            return _with_note(render.walk_text(graph.walk(conn, fn, "callers", depth), limit), note)

    @server.tool()
    def callees(symbol: str, depth: int = 3, limit: int = 40) -> str:
        """Functions this one calls, directly or through other functions, up to `depth` steps."""
        conn, note = state.open()
        with closing(conn):
            fn, msg = resolve(conn, symbol)
            if fn is None:
                return _with_note(msg, note)
            return _with_note(render.walk_text(graph.walk(conn, fn, "callees", depth), limit), note)

    @server.tool()
    def find(query: str, limit: int = 20) -> str:
        """Find functions by exact name, part of a name, ns::Class::method, name@file, or file.c:LINE."""
        conn, note = state.open()
        with closing(conn):
            fns = symbols.find(conn, query) or symbols.search(conn, query, limit)
            if not fns:
                return _with_note(f"No function matches '{query}'.", note)
            lines = [f"{f.qualified_name}@{f.file}  {f.where}  {f.signature}" for f in fns[:limit]]
            return _with_note("\n".join(lines), note)

    @server.tool()
    def index_status() -> str:
        """What AeroTrace indexed (files, functions, compile flags, libclang) and what it could not see."""
        conn, note = state.open()
        with closing(conn):
            meta = store.read_meta(conn)
            n = {
                t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("functions", "variables")
            }
            ptr = conn.execute("SELECT COUNT(*) FROM blind_spots WHERE kind = 'pointer_call'").fetchone()[0]
            flags = meta["compdb_path"] or "guessed (no compile_commands.json)"
            text = "\n".join(
                [
                    f"Repo: {root.name}",
                    f"Indexed {meta['units']} source files: {n['functions']} functions, {n['variables']} variables.",
                    f"Compile flags: {flags}.",
                    f"Files with parse errors: {meta['files_with_errors']}.",
                    f"Call sites through pointers: {ptr} (matched by type where possible).",
                    f"libclang: {meta['libclang']}.",
                ]
            )
            return _with_note(text, note)

    return server


def run(root: Path, build_dir: Path | None = None) -> None:
    logging.basicConfig(level=logging.WARNING, format="aerotrace: %(message)s")  # stderr only
    create_server(root, build_dir).run()
