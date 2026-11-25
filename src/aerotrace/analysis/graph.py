"""Callers, callees and blast radius over the index. Everything here is deterministic."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from aerotrace.analysis import symbols
from aerotrace.analysis.symbols import Fn

# From strongest to weakest. A path is as weak as its weakest link.
CONFIDENCE_ORDER = ("exact", "calls_removed", "uses_changed", "virtual", "ambiguous", "via_pointer", "data")
ALL_CALL_CONFIDENCE = ("exact", "virtual", "ambiguous", "via_pointer")
CONFIDENCE_MEANING = {
    "exact": "a direct call the compiler resolved",
    "calls_removed": "still calls a function this change removes (it will not build or link)",
    "uses_changed": "reads or writes a global or field whose declaration changed",
    "virtual": "a virtual call that may land on this override",
    "ambiguous": "more than one function has this name; this is one of them",
    "via_pointer": "a call through a function pointer of the matching type",
    "data": "reads data the changed function writes",
}


def weaker(a: str, b: str) -> str:
    return a if CONFIDENCE_ORDER.index(a) >= CONFIDENCE_ORDER.index(b) else b


@dataclass
class Hit:
    fn: Fn
    distance: int
    confidence: str  # weakest link on the best path found
    via: str | None  # the function one step closer to the start (its qualified name)
    lines: list[int]  # where, in the closer function or in this one, the link is
    via_id: str | None = None  # the id behind `via` (a function id, or a label key for seeds)


@dataclass
class Walk:
    start: Fn
    direction: str  # "callers" | "callees"
    depth: int
    hits: list[Hit] = field(default_factory=list)
    truncated_at_depth: bool = False  # more functions exist beyond `depth`
    unknown: list[str] = field(default_factory=list)  # callers that could not be followed (UNKNOWN notes)

    def by_distance(self) -> dict[int, list[Hit]]:
        out: dict[int, list[Hit]] = {}
        for h in self.hits:
            out.setdefault(h.distance, []).append(h)
        return out


def _neighbours(
    conn: sqlite3.Connection, ids: list[str], direction: str, allowed: tuple[str, ...]
) -> dict[str, list[tuple[str, str, int]]]:
    """For each id: [(neighbour id, confidence, line)] one call step away, in a stable order."""
    near, far = ("dst", "src") if direction == "callers" else ("src", "dst")
    marks_c = ",".join("?" * len(allowed))
    out: dict[str, list[tuple[str, str, int]]] = {}
    for i in range(0, len(ids), 400):
        chunk = ids[i : i + 400]
        marks = ",".join("?" * len(chunk))
        for r in conn.execute(
            f"""SELECT {near} AS a, {far} AS b, confidence, line FROM edges
                WHERE kind = 'calls' AND {near} IN ({marks}) AND confidence IN ({marks_c})
                ORDER BY a, b, confidence, line""",
            [*chunk, *allowed],
        ):
            out.setdefault(r["a"], []).append((r["b"], r["confidence"], r["line"]))
    return out


def walk_many(
    conn: sqlite3.Connection,
    starts: list[Fn],
    direction: str,
    depth: int,
    allowed: tuple[str, ...] = ALL_CALL_CONFIDENCE,
    seeds: dict[str, tuple[str, str, list[int]]] | None = None,
    labels: dict[str, str] | None = None,
) -> tuple[list[Hit], bool]:
    """Breadth-first walk over call links from every function in `starts` at once. Each function appears
    once, at its shortest distance from any start; among equally short paths, the strongest confidence wins.
    Start functions are never hits.

    `seeds` adds functions at distance 1 that are linked some other way: {id: (confidence, via id, lines)}.
    `labels` names `via` ids that are not functions (for example a changed variable).
    Returns (hits nearest first, whether more functions exist beyond `depth`).
    """
    rank = CONFIDENCE_ORDER.index
    start_ids = sorted({f.id for f in starts})
    # id -> (distance, confidence of the best path, the function one step closer, lines of that link)
    best: dict[str, tuple[int, str, str | None, list[int]]] = {i: (0, "exact", None, []) for i in start_ids}
    seed_ids = sorted(f for f in (seeds or {}) if f not in best)
    for fid in seed_ids:
        conf, via, lines = seeds[fid]  # type: ignore[index]
        best[fid] = (1, conf, via, sorted(set(lines)))

    frontier = start_ids
    reached_depth = False
    for distance in range(1, depth + 1):
        nbrs = _neighbours(conn, frontier, direction, allowed)
        found: dict[str, tuple[int, str, str | None, list[int]]] = {}
        for a in frontier:
            path_conf = best[a][1]
            for b, conf, line in nbrs.get(a, []):
                if b in best and best[b][0] < distance:
                    continue  # already reached by a shorter path
                c = weaker(path_conf, conf)
                cur = found.get(b) or best.get(b)
                if cur is None or rank(c) < rank(cur[1]):
                    found[b] = (distance, c, a, [line])
                elif cur[1] == c and cur[2] == a and line not in cur[3]:
                    cur[3].append(line)
        best.update(found)
        frontier = sorted(set(found) | (set(seed_ids) if distance == 1 else set()))
        if not frontier:
            break
        reached_depth = distance == depth

    truncated = False
    if reached_depth:
        more = _neighbours(conn, frontier, direction, allowed)
        truncated = any(b not in best for links in more.values() for b, _, _ in links)

    fns = symbols.get_many(conn, list(best))
    names = {i: f.qualified_name for i, f in fns.items()} | (labels or {})
    hits = [
        Hit(fns[i], d, c, names.get(v) if v else None, sorted(ls), v)
        for i, (d, c, v, ls) in best.items()
        if d > 0 and i in fns
    ]
    hits.sort(
        key=lambda h: (h.distance, CONFIDENCE_ORDER.index(h.confidence), h.fn.file, h.fn.line_start, h.fn.id)
    )
    return hits, truncated


def walk(
    conn: sqlite3.Connection,
    start: Fn,
    direction: str,
    depth: int,
    allowed: tuple[str, ...] = ALL_CALL_CONFIDENCE,
    extra_first_step: dict[str, tuple[str, list[int]]] | None = None,
) -> Walk:
    """Walk from one function. `extra_first_step` adds functions at distance 1 with a given confidence
    (used for data coupling)."""
    seeds = {fid: (conf, start.id, lines) for fid, (conf, lines) in (extra_first_step or {}).items()}
    hits, truncated = walk_many(conn, [start], direction, depth, allowed, seeds)
    unknown = template_notes(conn, [start, *(h.fn for h in hits)]) if direction == "callers" else []
    return Walk(start, direction, depth, hits, truncated, unknown)


def template_notes(conn: sqlite3.Connection, fns: list[Fn], limit: int = 3) -> list[str]:
    """UNKNOWN notes for calls in templates that may reach one of `fns` by name but are not linked, because
    their target depends on the template type (#33). One note per name, the first `limit` names."""
    names = sorted({f.name for f in fns})
    if not names:
        return []
    rows = conn.execute(
        """SELECT b.detail, f.qualified_name, f.file, b.line FROM blind_spots b
           JOIN functions f ON f.id = b.function_id WHERE b.kind = 'template_call'
           ORDER BY b.detail, f.file, b.line, f.qualified_name"""
    ).fetchall()
    by_name: dict[str, list[str]] = {}
    for r in rows:
        name = r["detail"].split(":", 1)[0]
        by_name.setdefault(name, []).append(f"{r['qualified_name']} ({r['file']}:{r['line']})")
    hit = [n for n in names if n in by_name]
    notes = []
    for n in hit[:limit]:
        where = by_name[n]
        shown = ", ".join(where[:3]) + (f" and {len(where) - 3} more" if len(where) > 3 else "")
        notes.append(
            f"UNKNOWN (#33): {len(where)} call(s) to a function named {n} inside templates depend on the "
            f"template type and are not linked, so they may be missing callers: {shown}."
        )
    if len(hit) > limit:
        notes.append(f"UNKNOWN (#33): template calls to {len(hit) - limit} more names are not linked either.")
    return notes


# ---------------------------------------------------------------- blast radius


@dataclass
class Impact:
    target: Fn
    callers: Walk  # everything upstream, including functions coupled through data
    data_readers: dict[str, list[str]]  # reader qualified name -> variables it reads that the target writes
    writes: list[str]  # variables the target writes
    blind_spots: list[str]  # plain-English limits of this answer


def data_coupling(
    conn: sqlite3.Connection, fid: str
) -> tuple[list[str], dict[str, tuple[set[str], list[int]]]]:
    """Variables `fid` writes, and the other functions that read them: {reader id: ({var names}, [lines])}."""
    writes = [
        r[0]
        for r in conn.execute(
            """SELECT DISTINCT v.qualified_name FROM edges e JOIN variables v ON v.id = e.dst
               WHERE e.src = ? AND e.kind = 'writes' ORDER BY 1""",
            (fid,),
        )
    ]
    readers: dict[str, tuple[set[str], list[int]]] = {}
    for r in conn.execute(
        """SELECT r.src AS reader, v.qualified_name AS var, r.line AS line
           FROM edges w JOIN edges r ON r.dst = w.dst AND r.kind = 'reads'
           JOIN variables v ON v.id = w.dst
           WHERE w.src = ? AND w.kind = 'writes' AND r.src != ?
           ORDER BY reader, var, line""",
        (fid, fid),
    ):
        names, lines = readers.setdefault(r["reader"], (set(), []))
        names.add(r["var"])
        lines.append(r["line"])
    return writes, readers


def impact(conn: sqlite3.Connection, target: Fn, depth: int = 5) -> Impact:
    """What could break if `target` changes: its callers (transitively, up to `depth`), and functions
    that read data it writes, plus their callers."""
    writes, readers = data_coupling(conn, target.id)
    extra = {rid: ("data", sorted(set(lines))) for rid, (_, lines) in readers.items()}
    callers = walk(conn, target, "callers", depth, extra_first_step=extra)
    names = symbols.get_many(conn, list(readers))
    data_readers = {names[r].qualified_name: sorted(v) for r, (v, _) in readers.items() if r in names}
    return Impact(
        target, callers, dict(sorted(data_readers.items())), writes, blind_spots_for(conn, target, callers)
    )


def blind_spots_for(conn: sqlite3.Connection, target: Fn, callers: Walk) -> list[str]:
    """Plain-English reasons the caller list may be incomplete."""
    notes: list[str] = []
    taken = conn.execute("SELECT type FROM address_taken WHERE function_id = ?", (target.id,)).fetchone()
    if taken is not None:
        n_ptr = sum(1 for h in callers.hits if h.distance == 1 and h.confidence == "via_pointer")
        if n_ptr:
            notes.append(
                f"{target.name} is used as a function pointer. {n_ptr} caller(s) were matched by type; "
                "calls from outside this repo (callbacks into libraries) are not visible."
            )
        else:
            notes.append(
                f"{target.name} is used as a function pointer, but no call site of type '{taken[0]}' was found. "
                "It may be called from outside the repo, or through a cast."
            )
    if target.clash:
        notes.append(
            f"Another function has the same clang ID as {target.name}. Callers that could not tell them "
            "apart are marked 'ambiguous'."
        )
    if target.linkage == "external":
        unknown = conn.execute(
            "SELECT COUNT(*) FROM blind_spots WHERE kind = 'pointer_call' AND detail LIKE '%target unknown%'"
        ).fetchone()[0]
        if unknown:
            notes.append(
                f"{unknown} call sites in the repo go through pointers of unknown type, or names that "
                "depend on a template type, and could reach anything."
            )
    notes += callers.unknown
    if callers.truncated_at_depth:
        notes.append(f"Stopped at depth {callers.depth}; more callers exist further up.")
    return notes + index_limits(conn)


def index_limits(conn: sqlite3.Connection) -> list[str]:
    """Plain-English limits that apply to every answer from this index."""
    notes: list[str] = []
    meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    if meta.get("flags_source") == "guessed":
        notes.append("Compile flags were guessed (no compile_commands.json), so some code may be missing.")
    n_err = int(meta.get("files_with_errors", "0"))
    if n_err:
        notes.append(
            f"{n_err} source file{'s' if n_err != 1 else ''} had parse errors; links in them may be missing."
        )
    notes += flag_limits(meta)
    notes.append("Only one build configuration was analysed; code under other #ifdef settings is not seen.")
    notes.append(
        "Writes made inside another function through a pointer (foo(&x)) are not seen; atomics and "
        "memset/memcpy-style calls are."
    )
    return notes


def _names(items: list[str], n: int = 3) -> str:
    return ", ".join(items[:n]) + (", ..." if len(items) > n else "")


def outside_build_note(meta: dict[str, str]) -> str | None:
    """The limit for repo sources that compile_commands.json does not list (None when there are none)."""
    outside = json.loads(meta.get("not_in_compdb") or "[]")
    if not outside:
        return None
    return (
        f"{len(outside)} C/C++ source file{'s are' if len(outside) != 1 else ' is'} not in "
        f"compile_commands.json and {'were' if len(outside) != 1 else 'was'} not analysed ({_names(outside)})."
    )


def build_coverage(conn: sqlite3.Connection) -> dict:
    """How much of the build the index covers: files in compile_commands.json, how many were parsed, how
    many had parse errors, and the sorted repo sources outside the build."""
    meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    parsed = conn.execute("SELECT COUNT(*) FROM files WHERE is_unit = 1").fetchone()[0]
    return {
        "build_files": int(meta.get("units") or parsed),
        "build_files_checked": parsed,
        "build_files_with_errors": int(meta.get("files_with_errors") or 0),
        "outside_build": sorted(json.loads(meta.get("not_in_compdb") or "[]")),
    }


def flag_limits(meta: dict[str, str]) -> list[str]:
    """Plain-English limits that come from the compile flags (see `aerotrace doctor` for details)."""
    notes: list[str] = []
    alternatives = json.loads(meta.get("compdb_alternatives") or "[]")
    if alternatives:
        notes.append(
            f"Used {meta.get('compdb_path')}; {len(alternatives)} other compile_commands.json "
            f"({_names(alternatives)}) were not used. Pick one with --build-dir."
        )
    outside = outside_build_note(meta)
    if outside:
        notes.append(outside)
    missing = json.loads(meta.get("missing_headers") or "[]")
    if missing:
        shown = ", ".join(f"{h} ({n} file{'s' if n != 1 else ''})" for h, n in missing[:3])
        notes.append(f"Some headers were not found: {shown}. Code that needs them may be missing links.")
    dropped = int(meta.get("machine_flags_dropped") or 0)
    if dropped:
        notes.append(
            f"{dropped} file{'s were' if dropped != 1 else ' was'} parsed without their -m target flags, "
            "so #ifdefs that depend on the target may differ from the real build."
        )
    return notes
