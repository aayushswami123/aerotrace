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
class SignatureImpact:
    target: Fn
    params: int | None  # None when the signature can't be read
    variadic: bool
    returns_void: bool
    sites: list[Site] = field(default_factory=list)  # direct, virtual and same-name calls
    pointer_sites: list[Site] = field(default_factory=list)  # calls through pointers of its type
    address_sites: list[AddressSite] = field(default_factory=list)
    upstream: list[Hit] = field(default_factory=list)  # callers of the callers, to `depth`
    depth: int = 2
    truncated: bool = False
    blind_spots: list[str] = field(default_factory=list)

    def uses(self) -> Counter:
        return Counter(s.use for s in self.sites + self.pointer_sites)


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
    starts = {s.caller.id: s.caller for s in res.sites + res.pointer_sites}
    if starts and depth > 0:
        hits, res.truncated = graph.walk_many(conn, list(starts.values()), "callers", depth)
        res.upstream = [h for h in hits if h.fn.id != target.id]
    res.blind_spots = _limits(conn, res)
    return res


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
