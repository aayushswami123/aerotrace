"""Turn what an agent types ("setCommand", "Shape::area", "src/t_string.c:440") into functions.

We never pick one silently: when a name matches more than one function, every match is returned and
the caller decides (the MCP tools show the list and ask the agent to be more specific).
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

FN_COLS = "id, name, qualified_name, file, line_start, line_end, signature, linkage, clash"


@dataclass(frozen=True)
class Fn:
    id: str
    name: str
    qualified_name: str
    file: str
    line_start: int
    line_end: int
    signature: str
    linkage: str
    clash: bool

    @property
    def where(self) -> str:
        return f"{self.file}:{self.line_start}"

    def brief(self) -> dict:
        return {
            "name": self.qualified_name,
            "file": self.file,
            "line": self.line_start,
            "signature": self.signature,
        }


def _row(r: sqlite3.Row) -> Fn:
    return Fn(
        r["id"],
        r["name"],
        r["qualified_name"],
        r["file"],
        r["line_start"],
        r["line_end"],
        r["signature"],
        r["linkage"],
        bool(r["clash"]),
    )


def get(conn: sqlite3.Connection, fid: str) -> Fn | None:
    r = conn.execute(f"SELECT {FN_COLS} FROM functions WHERE id = ?", (fid,)).fetchone()
    return _row(r) if r else None


def get_many(conn: sqlite3.Connection, ids: list[str]) -> dict[str, Fn]:
    out: dict[str, Fn] = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i : i + 500]
        marks = ",".join("?" * len(chunk))
        for r in conn.execute(f"SELECT {FN_COLS} FROM functions WHERE id IN ({marks})", chunk):
            out[r["id"]] = _row(r)
    return out


_FILE_LINE = re.compile(r"^(?P<file>[^:]+):(?P<line>\d+)$")


def find(conn: sqlite3.Connection, query: str) -> list[Fn]:
    """Functions matching `query`, best match kind first, sorted by file and line.

    Accepted forms: `file.c:123` (the function containing that line), a qualified name (`ns::Cls::f`),
    a plain name (`f`), or `name@file` to pick one of several functions with the same name.
    """
    q = query.strip()
    m = _FILE_LINE.match(q)
    if m:
        rows = conn.execute(
            f"""SELECT {FN_COLS} FROM functions
                WHERE (file = ? OR file LIKE ?) AND line_start <= ? AND line_end >= ?
                ORDER BY line_end - line_start, file""",
            (m["file"], f"%/{m['file']}", int(m["line"]), int(m["line"])),
        ).fetchall()
        # the innermost function in each matching file; several files can share a name (prog_a/util.c, prog_b/util.c)
        innermost: dict[str, Fn] = {}
        for r in rows:
            innermost.setdefault(r["file"], _row(r))
        return sorted(innermost.values(), key=lambda f: (f.file, f.line_start))
    file = None
    if "@" in q and not q.startswith("c:"):
        q, file = q.rsplit("@", 1)
    for col in ("id", "qualified_name", "name"):
        sql = f"SELECT {FN_COLS} FROM functions WHERE {col} = ?"
        args: list = [q]
        if file:
            sql += " AND (file = ? OR file LIKE ?)"
            args += [file, f"%/{file}"]
        rows = conn.execute(sql + " ORDER BY file, line_start", args).fetchall()
        if rows:
            return [_row(r) for r in rows]
    return []


def search(conn: sqlite3.Connection, text: str, limit: int = 20) -> list[Fn]:
    """Functions whose name contains `text` (for when the agent doesn't know the exact name)."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    rows = conn.execute(
        f"""SELECT {FN_COLS} FROM functions WHERE name LIKE ? ESCAPE '\\'
            ORDER BY (name = ?) DESC, length(name), file, line_start LIMIT ?""",
        ("%" + escaped + "%", text, limit),
    ).fetchall()
    return [_row(r) for r in rows]
