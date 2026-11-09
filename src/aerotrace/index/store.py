"""The index on disk: one SQLite file at `<repo>/.aerotrace/index.db`.

See docs/decisions/0003-index-in-dot-aerotrace.md. The schema version is bumped whenever a table changes;
an index with another version is rebuilt, never read.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from aerotrace.index.merge import Graph

SCHEMA_VERSION = "2"
DIR_NAME = ".aerotrace"
DB_NAME = "index.db"

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE files (
    path TEXT PRIMARY KEY,          -- repo-relative
    hash TEXT NOT NULL,             -- sha256 of the content
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    is_unit INTEGER NOT NULL,       -- 1 = a source file we parsed, 0 = a header it included
    error_count INTEGER NOT NULL DEFAULT 0,
    errors_json TEXT NOT NULL DEFAULT '[]',
    configs INTEGER NOT NULL DEFAULT 1   -- compile database entries for this file (we parse the first)
);
CREATE TABLE functions (
    id TEXT PRIMARY KEY,
    usr TEXT NOT NULL,
    name TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    container TEXT,
    file TEXT NOT NULL,
    line_start INTEGER NOT NULL,
    line_end INTEGER NOT NULL,
    signature TEXT NOT NULL,
    linkage TEXT NOT NULL,          -- internal (static) | external
    is_virtual INTEGER NOT NULL,
    is_pure_virtual INTEGER NOT NULL,
    is_public INTEGER NOT NULL,
    clash INTEGER NOT NULL,         -- 1 = another definition has the same USR
    external_calls INTEGER NOT NULL -- calls to code outside the repo (libc, std::, ...)
);
CREATE TABLE variables (
    id TEXT PRIMARY KEY,
    usr TEXT NOT NULL,
    name TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    container TEXT,
    kind TEXT NOT NULL,             -- field | global | static_local
    type TEXT NOT NULL,
    file TEXT NOT NULL,
    line INTEGER NOT NULL,
    clash INTEGER NOT NULL
);
CREATE TABLE edges (
    src TEXT NOT NULL,
    dst TEXT NOT NULL,
    kind TEXT NOT NULL,             -- calls | overrides | reads | writes
    confidence TEXT NOT NULL,       -- exact | virtual | ambiguous | via_pointer
    line INTEGER NOT NULL           -- where in src's file (0 when not about one line)
);
CREATE TABLE blind_spots (
    function_id TEXT NOT NULL,
    kind TEXT NOT NULL,             -- pointer_call | unresolved_call
    line INTEGER NOT NULL,
    detail TEXT NOT NULL
);
CREATE TABLE address_taken (function_id TEXT PRIMARY KEY, type TEXT NOT NULL);
CREATE TABLE class_bases (class TEXT NOT NULL, base TEXT NOT NULL);
CREATE INDEX edges_src ON edges (src, kind);
CREATE INDEX edges_dst ON edges (dst, kind);
CREATE INDEX functions_name ON functions (name);
CREATE INDEX functions_qname ON functions (qualified_name);
CREATE INDEX functions_file ON functions (file, line_start);
CREATE INDEX variables_name ON variables (name);
CREATE INDEX blind_fn ON blind_spots (function_id);
"""


def index_dir(root: Path) -> Path:
    return root / DIR_NAME


def db_path(root: Path) -> Path:
    return index_dir(root) / DB_NAME


def ensure_index_dir(root: Path) -> Path:
    """Create `.aerotrace/` with a .gitignore of `*`, so the index is never committed."""
    d = index_dir(root)
    d.mkdir(exist_ok=True)
    gi = d / ".gitignore"
    if not gi.exists():
        gi.write_text("# Created by AeroTrace. The index is local to this machine.\n*\n", encoding="utf-8")
    return d


def write_index(root: Path, graph: Graph, files: list[dict], meta: dict[str, str]) -> Path:
    """Write a fresh index next to the old one, then swap it in, so readers never see half an index."""
    ensure_index_dir(root)
    final = db_path(root)
    tmp = final.with_suffix(f".tmp{os.getpid()}")
    tmp.unlink(missing_ok=True)
    conn = sqlite3.connect(tmp)
    try:
        conn.executescript(SCHEMA)
        conn.executemany(
            "INSERT INTO meta VALUES (?, ?)", sorted({**meta, "schema_version": SCHEMA_VERSION}.items())
        )
        conn.executemany(
            "INSERT INTO files VALUES (:path, :hash, :size, :mtime_ns, :is_unit, :error_count, :errors_json, :configs)",
            files,
        )
        conn.executemany(
            """INSERT INTO functions VALUES (:id, :usr, :name, :qualified_name, :container, :file, :line_start,
               :line_end, :signature, :linkage, :is_virtual, :is_pure_virtual, :is_public, :clash, :external_calls)""",
            graph.functions,
        )
        conn.executemany(
            """INSERT INTO variables VALUES (:id, :usr, :name, :qualified_name, :container, :kind, :type, :file,
               :line, :clash)""",
            graph.variables,
        )
        conn.executemany("INSERT INTO edges VALUES (?, ?, ?, ?, ?)", graph.edges)
        conn.executemany("INSERT INTO blind_spots VALUES (?, ?, ?, ?)", graph.blind_spots)
        conn.executemany("INSERT INTO address_taken VALUES (?, ?)", graph.address_taken)
        conn.executemany(
            "INSERT INTO class_bases VALUES (?, ?)",
            [(c, b) for c, bs in graph.class_bases.items() for b in bs],
        )
        conn.commit()
    finally:
        conn.close()
    os.replace(tmp, final)
    return final


def connect(root: Path) -> sqlite3.Connection:
    """Open the index read-only. Raises FileNotFoundError when there is none."""
    path = db_path(root)
    if not path.is_file():
        raise FileNotFoundError(f"no AeroTrace index in {root}. Run `aerotrace index` first.")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    version = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    if version is None or version[0] != SCHEMA_VERSION:
        conn.close()
        raise FileNotFoundError(
            f"the index in {root} is from another AeroTrace version. Run `aerotrace index`."
        )
    return conn


def read_meta(conn: sqlite3.Connection) -> dict[str, str]:
    return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta ORDER BY key")}


def changed_files(conn: sqlite3.Connection, root: Path) -> list[str]:
    """Indexed files whose content changed (or that are gone) since the index was built."""
    from aerotrace.frontends.cpp.parse import file_hash

    out = []
    for r in conn.execute("SELECT path, hash, size, mtime_ns FROM files ORDER BY path"):
        p = root / r["path"]
        try:
            st = p.stat()
        except OSError:
            out.append(r["path"])
            continue
        if st.st_size == r["size"] and st.st_mtime_ns == r["mtime_ns"]:
            continue
        if st.st_size != r["size"] or file_hash(str(p)) != r["hash"]:
            out.append(r["path"])
    return out


def dump_canonical(conn: sqlite3.Connection) -> str:
    """A stable, human-readable text form of the graph, for golden tests and diffs."""
    lines: list[str] = []
    for r in conn.execute("SELECT * FROM functions ORDER BY file, line_start, id"):
        flags = [k for k in ("is_virtual", "is_pure_virtual", "clash") if r[k]]
        lines.append(
            f"fn {r['qualified_name']} {r['file']}:{r['line_start']}-{r['line_end']} {r['linkage']} {flags}"
        )
    for r in conn.execute("SELECT * FROM variables ORDER BY file, line, id"):
        lines.append(
            f"var {r['kind']} {r['qualified_name']} {r['file']}:{r['line']}{' clash' if r['clash'] else ''}"
        )
    name = {
        r["id"]: f"{r['qualified_name']}@{r['file']}"
        for r in conn.execute("SELECT id, qualified_name, file FROM functions")
    }
    name |= {
        r["id"]: f"{r['qualified_name']}@{r['file']}"
        for r in conn.execute("SELECT id, qualified_name, file FROM variables")
    }
    edge_lines = [
        f"edge {name.get(r['src'], r['src'])} -{r['kind']}/{r['confidence']}-> {name.get(r['dst'], r['dst'])} :{r['line']}"
        for r in conn.execute("SELECT * FROM edges")
    ]
    lines += sorted(edge_lines)
    lines += sorted(
        f"blind {name.get(r['function_id'], r['function_id'])} {r['kind']} :{r['line']} {r['detail']}"
        for r in conn.execute("SELECT * FROM blind_spots")
    )
    lines += sorted(
        f"address_taken {name.get(r[0], r[0])} {r[1]}"
        for r in conn.execute("SELECT function_id, type FROM address_taken")
    )
    return "\n".join(lines) + "\n"


def errors_json(errors: list[str]) -> str:
    return json.dumps(errors)
