"""What a change to a function's signature affects: its parameters (added, removed, reordered, retyped) or
its return type.

- Every call site: where, how many arguments it passes, and how it uses the return value.
- Every place the function is named as a value (handler tables, callbacks): the pointer type there must
  change too.
- Calls through pointers of the same type that may reach it.
- Callers further up, to a depth limit.
Each result says how sure it is (exact / probable / unknown) and why.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass, field

from aerotrace.analysis import certainty, graph, symbols
from aerotrace.analysis.graph import Hit
from aerotrace.analysis.symbols import Fn

# How a return value can be used, in the order they are listed.
USES = ("stored", "compared", "checked", "passed", "returned", "used", "called", "ignored", "discarded")
USE_WORDS = {
    "stored": "stored",
    "compared": "compared",
    "checked": "checked as true/false",
    "passed": "passed on",
    "returned": "returned",
    "used": "used in an expression",
    "called": "called",
    "ignored": "ignored",
    "discarded": "discarded with (void)",
}
BROKEN = "does not compile"
IGNORING = ("ignored", "discarded")

# Rank groups for call sites, most important first. A void function's calls are "no_result", never
# "ignores_result": there is no result to ignore.
RANK_GROUPS = ("does_not_compile", "uses_result", "ignores_result", "no_result")


@dataclass
class Site:
    caller: Fn
    line: int
    col: int
    n_args: int
    use: str
    detail: str
    link: str  # exact | ambiguous | virtual | via_pointer
    certainty: str
    reason: str

    @property
    def broken(self) -> bool:
        return self.detail.startswith(BROKEN)


@dataclass
class AddressSite:
    file: str
    line: int
    in_function: Fn | None
    in_variable: str | None
    how: str


@dataclass
class UpstreamGroup:
    """Callers further up that come in through one function that calls the target (the entry).

    `kind` is "direct" for every entry that calls the target directly (one group for all of them), or
    "pointer" for an entry that reaches it only through a function pointer (one group per entry)."""

    kind: str  # direct | pointer
    entry: Fn | None  # None for the direct group
    lines: list[int]  # the pointer-call lines in the entry (pointer groups)
    members: list[Hit]  # nearest first
    also: dict[str, int] = field(default_factory=dict)  # member id -> other entries at the same distance


@dataclass
class SignatureImpact:
    target: Fn
    params: int | None  # None when the signature can't be read
    variadic: bool
    returns_void: bool
    sites: list[Site] = field(default_factory=list)  # direct, virtual and same-name calls
    pointer_sites: list[Site] = field(default_factory=list)  # calls through pointers of its type
    address_sites: list[AddressSite] = field(default_factory=list)
    upstream: list[Hit] = field(default_factory=list)  # callers of the callers: distance 2 .. depth + 1
    groups: list[UpstreamGroup] = field(default_factory=list)  # `upstream`, grouped by entry
    depth: int = 2
    truncated: bool = False
    blind_spots: list[str] = field(default_factory=list)

    def uses(self, sites: list[Site] | None = None) -> Counter:
        """How the return value is used, over `sites` (default: direct and pointer call sites together)."""
        return Counter(s.use for s in (self.sites + self.pointer_sites if sites is None else sites))

    def rank_group(self, s: Site) -> str:
        if s.broken:
            return "does_not_compile"
        if self.returns_void:
            return "no_result"
        return "ignores_result" if s.use in IGNORING else "uses_result"

    def ranked(self, sites: list[Site]) -> list[Site]:
        """Calls that do not compile, then calls that use the result, then calls that ignore it. Inside a
        group: exact before probable before unknown, then file, line, column."""
        return sorted(
            sites,
            key=lambda s: (
                RANK_GROUPS.index(self.rank_group(s)),
                certainty.LEVELS.index(s.certainty),
                s.caller.file,
                s.line,
                s.col,
                s.caller.id,
            ),
        )

    def through_pointers(self) -> int:
        """Callers further up whose best path has a pointer call somewhere on it (so they are probable)."""
        return sum(1 for h in self.upstream if h.confidence == "via_pointer")

    def files(self) -> list[tuple[str, int]]:
        """Every file with direct call sites, and how many: most first, then by name."""
        counts = Counter(s.caller.file for s in self.sites)
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def parameters(signature: str, name: str) -> tuple[int | None, bool, bool]:
    """(number of parameters, variadic?, returns void?) from a stored signature such as
    'int parse_num(const char *, int *)', 'int area() const' or 'Square(int)'. (None, False, False) when it
    can't be read."""
    at = signature.find(name + "(")
    if at < 0:
        return None, False, False
    ret = signature[:at].strip()
    i, depth = at + len(name), 0
    for j in range(i, len(signature)):
        if signature[j] == "(":
            depth += 1
        elif signature[j] == ")":
            depth -= 1
            if depth == 0:
                break
    else:
        return None, False, False
    inner = signature[i + 1 : j].strip()
    returns_void = ret.split()[-1:] == ["void"] and "*" not in ret
    if inner in ("", "void"):
        return 0, False, returns_void
    parts, depth, cur = [], 0, ""
    for ch in inner:
        if ch in "(<[":
            depth += 1
        elif ch in ")>]":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    parts.append(cur.strip())
    variadic = parts[-1] == "..."
    return len(parts) - variadic, variadic, returns_void


def _sites(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> list[Site]:
    callers = symbols.get_many(conn, sorted({r["caller"] for r in rows}))
    out = []
    for r in rows:
        fn = callers.get(r["caller"])
        if fn is None:
            continue
        level, reason = certainty.of_call(r["confidence"])
        if r["detail"].startswith(BROKEN):
            reason = "calls it, but the call does not compile now (its arguments do not match)"
        elif r["confidence"] == "via_pointer":
            reason = f"calls a function pointer of type '{r['via_type']}'"
        out.append(
            Site(fn, r["line"], r["col"], r["n_args"], r["use"], r["detail"], r["confidence"], level, reason)
        )
    out.sort(key=lambda s: (certainty.LEVELS.index(s.certainty), s.caller.file, s.line, s.col, s.caller.id))
    return out


def signature_impact(conn: sqlite3.Connection, target: Fn, depth: int = 2) -> SignatureImpact:
    params, variadic, returns_void = parameters(target.signature, target.name)
    res = SignatureImpact(target, params, variadic, returns_void, depth=depth)
    res.sites = _sites(
        conn,
        conn.execute(
            "SELECT * FROM call_sites WHERE callee = ? ORDER BY caller, line, col", (target.id,)
        ).fetchall(),
    )
    taken = conn.execute("SELECT type FROM address_taken WHERE function_id = ?", (target.id,)).fetchone()
    if taken is not None and taken["type"]:
        res.pointer_sites = _sites(
            conn,
            conn.execute(
                """SELECT * FROM call_sites WHERE callee IS NULL AND via_type = ? AND caller != ?
                   ORDER BY caller, line, col""",
                (taken["type"], target.id),
            ).fetchall(),
        )
    rows = conn.execute(
        "SELECT * FROM address_sites WHERE function_id = ? ORDER BY file, line, how", (target.id,)
    ).fetchall()
    in_fns = symbols.get_many(conn, sorted({r["in_function"] for r in rows if r["in_function"]}))
    var_names = (
        {
            r["id"]: r["qualified_name"]
            for r in conn.execute(
                f"SELECT id, qualified_name FROM variables WHERE id IN ({','.join('?' * len(rows))})",
                [r["in_variable"] or "" for r in rows],
            )
        }
        if rows
        else {}
    )
    seen = set()
    for r in rows:
        key = (r["file"], r["line"], r["in_function"], r["in_variable"])
        if key in seen:
            continue
        seen.add(key)
        res.address_sites.append(
            AddressSite(
                r["file"],
                r["line"],
                in_fns.get(r["in_function"]) if r["in_function"] else None,
                var_names.get(r["in_variable"]) if r["in_variable"] else None,
                r["how"],
            )
        )
    if (res.sites or res.pointer_sites) and depth > 0:
        # Walk from the function itself, so a caller reached only through a pointer call keeps that
        # weaker link on its whole path. Distance 1 are the call sites listed above.
        hits, res.truncated = graph.walk_many(conn, [target], "callers", depth + 1)
        res.upstream = [h for h in hits if h.distance >= 2]
        res.groups = _groups(conn, res, hits)
    res.blind_spots = _limits(conn, res)
    return res


def _groups(conn: sqlite3.Connection, res: SignatureImpact, hits: list[Hit]) -> list[UpstreamGroup]:
    """Group the callers further up by the entry their path comes through: the function at distance 1
    (a caller of the target). A distance-1 function that calls the target directly at least once is a
    direct entry, even if it also calls it through a pointer.

    Each caller is put in exactly one group. Among its shortest, strongest paths (the ones the walk keeps),
    a direct entry wins, then the lowest entry id. `also` counts the other groups it is reached through at
    the same distance (any strength)."""
    by_id = {h.fn.id: h for h in hits}
    # entry ids reachable on the walk's best paths, and on any path of the same length
    best: dict[str, set[str]] = {h.fn.id: {h.fn.id} for h in hits if h.distance == 1}
    every: dict[str, set[str]] = {k: set(v) for k, v in best.items()}
    top = max((h.distance for h in hits), default=0)
    for d in range(2, top + 1):
        prev = sorted(h.fn.id for h in hits if h.distance == d - 1)
        nbrs = graph._neighbours(conn, prev, "callers", graph.ALL_CALL_CONFIDENCE)
        for a in prev:
            for b, conf, _ in nbrs.get(a, []):
                hb = by_id.get(b)
                if hb is None or hb.distance != d:
                    continue
                every.setdefault(b, set()).update(every.get(a, set()))
                if graph.weaker(by_id[a].confidence, conf) == hb.confidence:
                    best.setdefault(b, set()).update(best.get(a, set()))

    def is_direct(entry: str) -> bool:
        return by_id[entry].confidence != "via_pointer"

    def group_key(entry: str) -> str:
        return "" if is_direct(entry) else entry

    ptr_lines: dict[str, set[int]] = {}
    for s in res.pointer_sites:
        ptr_lines.setdefault(s.caller.id, set()).add(s.line)
    groups: dict[str, UpstreamGroup] = {}
    for h in res.upstream:
        fid = h.fn.id
        cands = best.get(fid) or every.get(fid) or set()
        if not cands:  # cannot happen: the walk reached it from some entry
            continue
        entry = min(cands, key=lambda e: (not is_direct(e), e))
        key = group_key(entry)
        g = groups.get(key)
        if g is None:
            if key == "":
                g = UpstreamGroup("direct", None, [], [])
            else:
                g = UpstreamGroup("pointer", by_id[entry].fn, sorted(ptr_lines.get(entry, set())), [])
            groups[key] = g
        g.members.append(h)
        others = {group_key(e) for e in every.get(fid, cands)} - {key}
        if others:
            g.also[fid] = len(others)
    out = [groups[k] for k in groups if k == ""]
    out += sorted(
        (g for k, g in groups.items() if k != ""),
        key=lambda g: (g.entry.file, g.lines[:1], g.entry.id),  # type: ignore[union-attr]
    )
    return out


def _limits(conn: sqlite3.Connection, res: SignatureImpact) -> list[str]:
    notes = []
    t = res.target
    if t.linkage == "external":
        notes.append(
            f"{t.name} can be called from outside this repo (other programs or libraries that link it); "
            "those calls are not visible."
        )
    if res.address_sites:
        notes.append(
            "Code that receives its address (a table or callback) calls it through a pointer. Those calls are "
            "matched by type; a call through a cast or a void * is not seen."
        )
    unknown = conn.execute(
        "SELECT COUNT(*) FROM call_sites WHERE callee IS NULL AND via_type = ''"
    ).fetchone()[0]
    if unknown:
        notes.append(
            f"{unknown} call sites in the repo go through pointers of unknown type and could reach anything."
        )
    if res.truncated:
        notes.append(f"Stopped at depth {res.depth}; more callers exist further up.")
    return notes + graph.index_limits(conn)
