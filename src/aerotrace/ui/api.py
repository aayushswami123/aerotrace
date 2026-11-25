"""What the dashboard (`aerotrace ui`) shows, as plain dicts. No HTTP here; `ui/server.py` serves these.

Read-only. Reads `.aerotrace/index.db` (through `index/store.py`, opened read-only) and the check answers
saved in `.aerotrace/checks/`. Nothing here decides what a change affects: that is `analysis/`, whose
answers are only counted and passed on.

Deterministic: the same index and the same saved checks give the same answers. The one exception, dates
(when the index was built, when a check was saved), is kept in `runinfo()` and nowhere else.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

from aerotrace.analysis.graph import build_coverage
from aerotrace.index import store
from aerotrace.ui import schema, terminal

CHECKS_DIR = "checks"
CHECK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
RECENT = 5

BLIND_SPOT_WORDS = {
    "pointer_call": "Calls through a function pointer",
    "unresolved_call": "Calls AeroTrace couldn't resolve",
}


class NotFound(LookupError):
    """The thing asked for doesn't exist (a check id that isn't saved, say)."""


def repo_name(root: Path) -> str:
    return root.resolve().name


def _checks_dir(root: Path) -> Path:
    return store.index_dir(root) / CHECKS_DIR


def _no_paths(text: str, root: Path) -> str:
    """Messages never carry machine paths: the repo folder becomes the repo name."""
    for p in {str(root.resolve()), str(root)}:
        text = text.replace(p, repo_name(root))
    return text


def _broken(root: Path, why: str) -> dict:
    return {
        "status": "index_error",
        "message": _no_paths(f"The index can't be read: {why}.", root),
        "fix": "aerotrace index",
    }


def _open(root: Path) -> tuple[sqlite3.Connection | None, dict]:
    """(connection, problem). The problem says what is wrong and how to fix it, in the words the CLI uses."""
    try:
        return store.connect(root), {}
    except store.IndexVersionError as e:
        return None, {
            "status": "index_error",
            "message": _no_paths(e.reason, root) + ".",
            "fix": "aerotrace index",
        }
    except FileNotFoundError:
        return None, {
            "status": "no_index",
            "message": "No index yet, so there is nothing to show.",
            "fix": "aerotrace index",
        }
    except sqlite3.DatabaseError as e:
        return None, _broken(root, str(e))


def _count(conn: sqlite3.Connection, sql: str, *args: object) -> int:
    return int(conn.execute(sql, args).fetchone()[0])


def overview(root: Path) -> dict:
    """Repo, index sizes, build coverage, blind spots and the most recent saved checks."""
    out: dict = {"repo": repo_name(root), "recent_checks": checks(root)["checks"][:RECENT]}
    conn, problem = _open(root)
    if conn is None:
        return {**out, **problem}
    try:
        return _overview(root, conn, out)
    except sqlite3.DatabaseError as e:  # opened, but a table is missing or damaged
        return {**out, **_broken(root, str(e))}
    finally:
        conn.close()


def _overview(root: Path, conn: sqlite3.Connection, out: dict) -> dict:
    meta = store.read_meta(conn)
    units = _count(conn, "SELECT COUNT(*) FROM files WHERE is_unit = 1")
    files = _count(conn, "SELECT COUNT(*) FROM files")
    blind = [
        {"kind": k, "label": BLIND_SPOT_WORDS.get(k, k.replace("_", " ").capitalize()), "count": n}
        for k, n in conn.execute("SELECT kind, COUNT(*) FROM blind_spots GROUP BY kind ORDER BY kind")
    ]
    escapes = _count(conn, "SELECT COUNT(*) FROM escapes")
    if escapes:
        blind.append({"kind": "escape", "label": "Data whose address escapes", "count": escapes})
    coverage = build_coverage(conn)
    changed = store.changed_files(conn, root)
    out.update(
        {
            "status": "ok",
            "tool": meta.get("tool_version") or meta.get("aerotrace_version") or "",
            "index": {
                "files": files,
                "parsed": units,
                "headers": files - units,
                "functions": _count(conn, "SELECT COUNT(*) FROM functions"),
                "variables": _count(conn, "SELECT COUNT(*) FROM variables"),
                "calls": _count(conn, "SELECT COUNT(*) FROM edges WHERE kind = 'calls'"),
                "data_uses": _count(conn, "SELECT COUNT(*) FROM edges WHERE kind IN ('reads', 'writes')"),
            },
            "coverage": {
                "build_files": coverage["build_files"],
                "checked": coverage["build_files_checked"],
                "with_errors": coverage["build_files_with_errors"],
                "outside_build": len(coverage["outside_build"]),
                "outside_examples": coverage["outside_build"][:8],
            },
            "blind_spots": sorted(blind, key=lambda b: (-b["count"], b["kind"])),
            "blind_spots_total": sum(b["count"] for b in blind),
            "changed_since_index": len(changed),
            "changed_examples": changed[:8],
        }
    )
    return out


def _verdict(data: dict) -> tuple[str, str]:
    """(icon key, headline) of a check, worded exactly as the terminal answer words it."""
    icon, lines = terminal.headline(data)
    return icon, lines[0] if lines else ""


def _level(data: dict) -> str:
    """The strongest status in a check, for its chip: breaks, may_break, affected, unknown or none."""
    s = data.get("summary") or {}
    if s.get("breaks_exact"):
        return "breaks"
    if s.get("breaks_probable"):
        return "may_break"
    if s.get("affected_direct") or s.get("affected_further"):
        return "affected"
    if s.get("unknown") or s.get("not_followed"):
        return "unknown"
    return "none"


def _reason(e: Exception) -> str:
    """Why a file couldn't be read, without its path."""
    if isinstance(e, json.JSONDecodeError):
        return f"not valid JSON (line {e.lineno})"
    if isinstance(e, OSError):
        return (e.strerror or type(e).__name__).lower()
    return str(e)


class Unreadable(ValueError):
    """A saved check exists but can't be read; the message has no path."""


def _changed_text(change: dict) -> str:
    """'1 changed function, 2 removed functions': what the check found changed, in words."""
    words = (
        ("changed_functions", "changed function"),
        ("new_functions", "new function"),
        ("removed_functions", "removed function"),
        ("changed_declarations", "changed declaration"),
    )
    parts = [f"{change[k]:,} {w}{'' if change[k] == 1 else 's'}" for k, w in words if change.get(k)]
    return ", ".join(parts)


def _load_check(path: Path) -> dict:
    return schema.check(json.loads(path.read_text(encoding="utf-8")))


def _check_files(root: Path) -> list[Path]:
    d = _checks_dir(root)
    if not d.is_dir():
        return []
    files = [p for p in d.glob("*.json") if not p.name.endswith(".run.json") and CHECK_ID.match(p.stem)]
    # by the run number first, so 10000 comes after 9999 (ids are NNNN-hash, NNNN grows past 4 digits)
    return sorted(
        files, key=lambda p: (int(m.group(1)) if (m := re.match(r"(\d+)-", p.stem)) else -1, p.stem)
    )


def _run_info(path: Path) -> dict:
    run = path.with_name(path.stem + ".run.json")
    try:
        return json.loads(run.read_text(encoding="utf-8")) if run.is_file() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def checks(root: Path) -> dict:
    """Saved checks, newest id first, each with its verdict line, counts and source (no dates)."""
    out = []
    for p in reversed(_check_files(root)):
        item: dict = {"id": p.stem, "source": _run_info(p).get("source") or None}
        try:
            data = _load_check(p)
        except (OSError, json.JSONDecodeError, schema.ContractError) as e:
            item.update({"ok": False, "problem": f"This saved check can't be read: {_reason(e)}"})
            out.append(item)
            continue
        icon, line = _verdict(data)
        s = data.get("summary") or {}
        item.update(
            {
                "ok": True,
                "icon": icon,
                "verdict": line,
                "level": _level(data),
                "counts": {
                    "breaks": s.get("breaks_exact", 0),
                    "may_break": s.get("breaks_probable", 0),
                    "affected": s.get("affected_direct", 0) + s.get("affected_further", 0),
                    "unknown": s.get("unknown", 0),
                    "not_followed": s.get("not_followed", 0),
                },
                "changed_functions": (data.get("change") or {}).get("changed_functions", 0),
                "changed": _changed_text(data.get("change") or {}),
            }
        )
        out.append(item)
    return {"checks": out, "dir": f"{store.DIR_NAME}/{CHECKS_DIR}"}


def check(root: Path, check_id: str) -> dict:
    """One saved check's full answer (the aerotrace-check JSON, as saved)."""
    if not CHECK_ID.match(check_id):
        raise NotFound(check_id)
    path = _checks_dir(root) / f"{check_id}.json"
    if path not in _check_files(root):
        raise NotFound(check_id)
    try:
        return _load_check(path)
    except (OSError, json.JSONDecodeError, schema.ContractError) as e:
        raise Unreadable(f"This saved check can't be read: {_reason(e)}") from None


def runinfo(root: Path) -> dict:
    """Dates, for display only: when the index was built and when each check was saved. Not an analysis
    answer; it changes from run to run."""
    db = store.db_path(root)
    indexed = (
        datetime.fromtimestamp(db.stat().st_mtime, UTC).isoformat(timespec="minutes")
        if db.is_file()
        else None
    )
    saved = {}
    for p in _check_files(root):
        date = _run_info(p).get("date")
        if isinstance(date, str):
            saved[p.stem] = date
    return {"indexed": indexed, "checks": saved}


# ---------------------------------------------------------------- the graph of one saved check

STATUS_ORDER = {
    "changed": 0,
    "new": 0,
    "removed": 0,
    "breaks": 1,
    "affected": 2,
    "unknown": 3,
    "no_effect": 4,
}
SNIPPET_AROUND = 4  # lines shown above and below the linked line
SNIPPET_MAX = 15


def _group(n: dict) -> str:
    """The status group a node is drawn and filtered by. MAY BREAK is a break whose link is not exact."""
    if n["status"] in ("changed", "new", "removed"):
        return "changed"
    if n["status"] == "breaks":
        return "breaks" if n["certainty"] == "exact" else "may_break"
    return n["status"]


def graph(root: Path, check_id: str) -> dict:
    """The nodes of one saved check, laid out in rings by distance from the change.

    Each node gets its ring (its distance) and an order inside the ring: the order of its parent (`via`)
    in the ring inside it, then status, then id. The browser places only the nodes it shows, in that
    order, so the same check always gives the same picture. Nothing here decides what is affected."""
    data = check(root, check_id)
    nodes = data.get("nodes") or []
    by_id = {n["id"]: n for n in nodes}
    rings: dict[int, list[dict]] = {}
    for n in nodes:
        rings.setdefault(int(n.get("distance") or 0), []).append(n)
    order: dict[str, int] = {}
    out = []
    for d in sorted(rings):
        ring = sorted(
            rings[d],
            key=lambda n: (
                order.get(n.get("via") or "", -1) if d else 0,
                STATUS_ORDER.get(n["status"], 9),
                n["file"],
                n["line"],
                n["id"],
            ),
        )
        for i, n in enumerate(ring):
            order[n["id"]] = i
            out.append(
                {
                    "id": n["id"],
                    "name": terminal.title(n),
                    "group": _group(n),
                    "word": schema.node_word(n["status"], n["certainty"]),
                    "ring": d,
                    "order": i,
                    "via": n["via"] if n.get("via") in by_id else None,
                    "certainty": n["certainty"],
                    "file": n["file"],
                    "line": n["line"],
                }
            )
    counts: dict[str, int] = {}
    for n in out:
        counts[n["group"]] = counts.get(n["group"], 0) + 1
    _, line = _verdict(data)
    return {
        "id": check_id,
        "verdict": line,
        "level": _level(data),
        "source": _run_info(_checks_dir(root) / f"{check_id}.json").get("source"),
        "nodes": out,
        "counts": counts,
        "omitted": (data.get("omitted") or {}).get("nodes", 0),
        "not_followed": (data.get("summary") or {}).get("not_followed", 0),
        "blind_spots": [b["text"] for b in (data.get("sections") or {}).get("blind_spots") or []],
    }


def _snippet(root: Path, rel: str, lines: list[int]) -> dict | None:
    """Source lines around the linked lines, only from files the index lists and only inside the repo."""
    conn, _ = _open(root)
    if conn is None:
        return None
    try:
        if rel not in store.indexed_paths(conn):
            return None
    finally:
        conn.close()
    base = root.resolve()
    path = (base / rel).resolve()
    if base not in path.parents or not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace").split("\n")
    first = max(1, min(lines) - SNIPPET_AROUND)
    last = min(len(text), max(lines) + SNIPPET_AROUND, first + SNIPPET_MAX - 1)
    return {
        "file": rel,
        "lines": [{"n": i, "text": text[i - 1], "hit": i in lines} for i in range(first, last + 1)],
    }


def node(root: Path, check_id: str, node_id: str) -> dict:
    """Everything the side panel shows for one node: where, why, how sure, code, and the path back."""
    data = check(root, check_id)
    by_id = {n["id"]: n for n in data.get("nodes") or []}
    n = by_id.get(node_id)
    if n is None:
        raise NotFound(node_id)
    path, seen, cur = [], set(), n
    while cur is not None and cur["id"] not in seen:
        seen.add(cur["id"])
        path.append({"id": cur["id"], "name": terminal.title(cur), "file": cur["file"], "line": cur["line"],
                     "word": schema.node_word(cur["status"], cur["certainty"]), "group": _group(cur)})  # fmt: skip
        cur = by_id.get(cur.get("via") or "")
    path.reverse()
    snippet = None
    if n["status"] != "removed":
        snippet = _snippet(root, n["file"], n.get("lines") or [n["line"]])
    return {
        "id": n["id"],
        "name": terminal.title(n),
        "group": _group(n),
        "word": schema.node_word(n["status"], n["certainty"]),
        "file": n["file"],
        "line": n["line"],
        "why": n.get("why") or "",
        "certainty": n["certainty"],
        "certainty_means": schema.CERTAINTY_MEANING.get(n["certainty"], ""),
        "link": n.get("link"),
        "distance": n.get("distance", 0),
        "via_name": n.get("via_name"),
        "path": path,
        "snippet": snippet,
        "snippet_note": "Removed by the change, so there is no current code to show."
        if n["status"] == "removed"
        else None,
    }


def benchmark() -> dict | None:
    """AeroTrace's own published benchmark (shipped in the package), or None when this build has none."""
    f = resources.files("aerotrace.ui") / "web" / "benchmark.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
