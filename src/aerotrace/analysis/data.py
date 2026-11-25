"""Who uses a struct field or a global: what a rename, removal or type change of it affects.

- Every function that reads or writes it, with the lines.
- Every global initializer that sets it ({.field = value}, often at file scope in handler tables).
- Every place that still uses it after it was removed (compile error "no member named").
- Every place its address goes where we can't follow it (foo(&x), p = &x, return &x): unknown.
- Globals of its struct type, whose initializers may set it by position: unknown.
- For a global: the callers of those functions, to a small depth.

Fields are tracked per struct TYPE, not per object. For a change to the field's declaration every access is
exact (the code names the field). For "f writes it, who reads it?" (the value question, `writer_id`), a
reader of a field is only probable: it may read another object of the same type.
"""

from __future__ import annotations

import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field

from aerotrace.analysis import certainty, graph, symbols
from aerotrace.analysis.graph import Hit
from aerotrace.analysis.symbols import Fn

DECLARATION = "declaration"  # rename, remove or retype the field or global
VALUE = "value"  # change what one function stores in it

GLOBAL_DEPTH = 2  # default: callers of the users, and their callers
FIELD_DEPTH = 0  # default: users only (a field change breaks the code that names it)

_VAR_COLS = "id, usr, name, qualified_name, container, kind, type, file, line, clash"
_UNNAMED = re.compile(r"^(?:struct|union|class|enum)? ?\((?:unnamed|anonymous)(?: \w+)? at (.+)\)$")


@dataclass(frozen=True)
class Var:
    """A field, global or static local from the `variables` table, or a field that is gone (exists=False:
    the index has no declaration, only compile errors that still use it)."""

    id: str
    usr: str
    name: str
    qualified_name: str
    container: str | None
    kind: str  # field | global | static_local
    type: str
    file: str
    line: int
    clash: bool
    display: str = ""  # the name people type: CallReply::val.str instead of the unnamed union's spelling
    exists: bool = True

    @property
    def shown(self) -> str:
        return self.display or self.qualified_name

    @property
    def where(self) -> str:
        return f"{self.file}:{self.line}" if self.exists else "not declared any more"

    @property
    def record(self) -> str:
        """The struct a field belongs to, without unnamed unions or structs in between."""
        return "::".join(p for p in (self.container or "").split("::") if _location(p) is None)

    @property
    def is_static(self) -> bool:
        """A file-scope static global: clang gives it a USR with the file name (c:lazyfree.c@name)."""
        return self.kind == "global" and not self.usr.startswith("c:@")

    @property
    def in_union(self) -> bool:
        return self.kind == "field" and bool(re.search(r"@U[Aa]?@", self.usr))

    def brief(self) -> dict:
        return {
            "name": self.shown,
            "kind": self.kind,
            "type": self.type,
            "file": self.file if self.exists else None,
            "line": self.line if self.exists else None,
            "exists": self.exists,
        }


@dataclass
class Use:
    """One function that reads and/or writes it."""

    fn: Fn
    reads: list[int]
    writes: list[int]
    certainty: str
    reason: str
    via_field: str = ""  # value question on a struct global: the field the writer writes

    @property
    def first(self) -> int:
        return min(self.reads + self.writes)


@dataclass
class InitSite:
    """A global's initializer sets this field: {.field = value}."""

    variable: Var
    file: str
    line: int
    certainty: str = certainty.EXACT
    reason: str = "sets it in a designated initializer"


@dataclass
class Broken:
    """A compile error "no member named X in S": code that still uses a field that is gone."""

    file: str
    line: int
    fn: Fn | None
    record: str
    certainty: str
    reason: str


@dataclass
class Escape:
    """Its address goes where we can't follow it."""

    fn: Fn  # for &x in a file-scope initializer: stands for the global (see `in_variable`)
    line: int
    detail: str
    certainty: str = certainty.UNKNOWN
    reason: str = ""
    in_variable: Var | None = None  # the global whose initializer stores the address (no function does it)


@dataclass
class DataUsers:
    target: Var
    question: str = DECLARATION
    writer: Fn | None = None  # value question: what reads what this function writes
    via_fields: list[Var] = field(default_factory=list)  # value question on a struct global: fields written
    uses: list[Use] = field(default_factory=list)  # ranked: writers first, then readers
    initializers: list[InitSite] = field(default_factory=list)
    by_position: list[Var] = field(
        default_factory=list
    )  # globals of its struct type (may set it by position)
    broken: list[Broken] = field(default_factory=list)
    escapes: list[Escape] = field(default_factory=list)
    upstream: list[Hit] = field(default_factory=list)  # callers of the users, distance 1..depth
    depth: int = 0
    truncated: bool = False
    struct_fields: list[tuple[str, int, int]] = field(
        default_factory=list
    )  # struct global: (field, users, writers)
    struct_fields_total: int = 0  # fields of its struct type that any function uses
    writer_writes: bool = True  # value question: the writer writes the variable itself
    same_name: list[Var] = field(default_factory=list)  # other fields/globals with this name, not included
    other_copies: list[Var] = field(default_factory=list)  # same clang ID, defined in another file
    blind_spots: list[str] = field(default_factory=list)

    @property
    def writers(self) -> list[Use]:
        return [u for u in self.uses if u.writes]

    def files(self) -> list[tuple[str, int]]:
        """(file, number of functions using it there), most first."""
        c = Counter(u.fn.file for u in self.uses)
        return sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))


# ---------------------------------------------------------------- finding what the agent means


def _row(r: sqlite3.Row, display: str = "") -> Var:
    return Var(
        r["id"],
        r["usr"],
        r["name"],
        r["qualified_name"],
        r["container"],
        r["kind"],
        r["type"],
        r["file"],
        r["line"],
        bool(r["clash"]),
        display,
    )


def _location(segment: str) -> str | None:
    m = _UNNAMED.match(segment)
    return m.group(1) if m else None


def _names(conn: sqlite3.Connection, r: sqlite3.Row) -> tuple[str, str]:
    """(display name, short name) for a variable. An unnamed struct or union in the scope is replaced by the
    member that holds it (CallReply::val.str); one with no member (C11 anonymous member) is skipped. The
    short name skips every unnamed part (CallReply::str)."""
    parts = r["qualified_name"].split("::")
    if not any(_location(p) for p in parts):
        return r["qualified_name"], r["qualified_name"]
    shown, sep = "", ""
    for i, p in enumerate(parts):
        loc = _location(p)
        if loc is None:
            shown, sep = shown + sep + p, "::"
            continue
        members = conn.execute(
            "SELECT name, type FROM variables WHERE kind = 'field' AND container = ? ORDER BY name",
            ("::".join(parts[:i]),),
        ).fetchall()
        member = next((m["name"] for m in members if m["type"].endswith(f" at {loc})")), None)
        if member is not None:
            shown, sep = shown + sep + member, "."
    return shown, "::".join(p for p in parts if _location(p) is None)


def _by_name(conn: sqlite3.Connection, name: str) -> list[tuple[Var, str]]:
    out = []
    for r in conn.execute(
        f"SELECT {_VAR_COLS} FROM variables WHERE name = ? ORDER BY file, line, id", (name,)
    ):
        display, short = _names(conn, r)
        out.append((_row(r, display if display != r["qualified_name"] else ""), short))
    return out


def _in_file(v: Var, file: str | None) -> bool:
    return file is None or v.file == file or v.file.endswith("/" + file)


def _struct_name(type_: str) -> str:
    """The struct a type names: 'struct redisServer' -> 'redisServer', 'const struct conn[4]' -> 'conn'."""
    t = re.sub(r"\[[^\]]*\]", "", type_)
    t = re.sub(r"\b(const|volatile|struct|union|class)\b", "", t)
    return t.strip()


def find_variables(conn: sqlite3.Connection, query: str) -> list[Var]:
    """Variables matching `query`. Accepted forms: `Struct::field`, `global`, `name@file`,
    `Struct::field@file`, `Struct::member.field` (a field inside an unnamed union or struct), and
    `global.field` (a field of a global's struct type). Several matches are all returned, never one picked
    silently. Fields of unnamed unions also match without the unnamed part (`CallReply::str`)."""
    q = query.strip()
    if not q:
        return []
    file = None
    if "@" in q and not q.startswith("c:"):
        q, file = q.rsplit("@", 1)
    r = conn.execute(f"SELECT {_VAR_COLS} FROM variables WHERE id = ?", (query.strip(),)).fetchone()
    if r is not None:
        return [_row(r, _names(conn, r)[0] if "(" in r["qualified_name"] else "")]
    last = re.split(r"::|\.", q)[-1]
    found = _by_name(conn, last)
    if "::" not in q and "." in q:  # global.field
        base = q.split(".", 1)[0]
        rest = q.split(".", 1)[1]
        out = []
        for g, _ in _by_name(conn, base):
            if g.kind == "field" or not _in_file(g, file):
                continue
            struct = _struct_name(g.type)
            for v, short in found:
                if v.kind == "field" and (
                    v.qualified_name == f"{struct}::{rest}"
                    or v.shown == f"{struct}::{rest}"
                    or short == f"{struct}::{last}"
                ):
                    out.append(v)
        if out:
            return sorted({v.id: v for v in out}.values(), key=lambda v: (v.file, v.line, v.id))
    if "::" not in q and "." not in q:
        # a bare name: every field, global and static local with that name (never one picked silently)
        return [v for v, _ in found if v.name == q and _in_file(v, file)]
    for match in (
        lambda v, s: v.qualified_name == q,
        lambda v, s: v.shown == q,
        lambda v, s: s == q,
    ):
        out = [v for v, s in found if match(v, s) and _in_file(v, file)]
        if out:
            return out
    return []


def gone_fields(conn: sqlite3.Connection, query: str) -> list[Var]:
    """Fields (and globals) the index has no declaration for, but that code still uses: compile errors "no
    member named X in S", "field designator X does not refer to any field in S", and for a bare name X
    "use of undeclared identifier X" (a removed global). `query` is `S::X` (or `X` for every record). They
    come back with exists=False."""
    q = query.strip().rsplit("@", 1)[0]
    record, _, member = q.rpartition("::")
    records = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT record FROM missing_members WHERE member = ? AND record != '' ORDER BY record",
            (member,),
        )
    ]
    undeclared = (
        conn.execute(
            "SELECT 1 FROM missing_members WHERE member = ? AND record = '' LIMIT 1", (member,)
        ).fetchone()
        is not None
    )
    gone_global = not record and undeclared
    if record:
        # asked for S::X: answer for S when an error names S, or when S is a known struct and errors name
        # another type (a typedef of S, a base; listed as probable) or name X alone (C++: X used without
        # this-> in a method). A typo'd S is "not found".
        known = record in records or (
            (records or undeclared)
            and conn.execute("SELECT 1 FROM variables WHERE container = ? LIMIT 1", (record,)).fetchone()
        )
        records = [record] if known else []
    out = [Var("", "", member, f"{r}::{member}", r, "field", "", "", 0, False, exists=False) for r in records]
    if gone_global:
        out.append(Var("", "", member, member, None, "global", "", "", 0, False, exists=False))
    return out


def suggest(conn: sqlite3.Connection, query: str, limit: int = 10) -> list[Var]:
    """Near matches when nothing is found: every variable with the last part of the name."""
    last = re.split(r"::|\.", query.strip().rsplit("@", 1)[0])[-1]
    return [v for v, _ in _by_name(conn, last)][:limit]


def _struct_fields(conn: sqlite3.Connection, var: Var) -> tuple[list[tuple[str, int, int]], int]:
    """For a struct global (server): its struct type's fields that functions use, most used first:
    (field name, functions using it, functions writing it), and how many fields are used in all."""
    struct = _struct_name(var.type)
    if not struct or "*" in var.type:
        return [], 0
    rows = conn.execute(
        """SELECT v.name AS name, COUNT(DISTINCT e.src) AS n,
                  COUNT(DISTINCT CASE WHEN e.kind = 'writes' THEN e.src END) AS w
           FROM edges e JOIN variables v ON v.id = e.dst
           WHERE v.kind = 'field' AND v.container = ? AND e.kind IN ('reads', 'writes')
           GROUP BY v.id ORDER BY n DESC, v.name""",
        (struct,),
    ).fetchall()
    return [(r["name"], r["n"], r["w"]) for r in rows[:5]], len(rows)


# ---------------------------------------------------------------- the answer


def _rank(u: Use) -> tuple:
    return (0 if u.writes else 1, certainty.LEVELS.index(u.certainty), u.fn.file, u.first, u.fn.id)


def _uses(
    conn: sqlite3.Connection,
    var: Var,
    question: str,
    skip: str | None = None,
    kinds=("reads", "writes"),
    not_values: set[tuple[str, int]] | None = None,
) -> list[Use]:
    """One Use per function. `not_values`: (function id, line) where the variable's address is taken
    (&x passed on or stored): that is not a read of its value, so those read lines are left out."""
    marks = ",".join("?" * len(kinds))
    rows = conn.execute(
        f"""SELECT src, kind, confidence, line FROM edges WHERE dst = ? AND kind IN ({marks})
            ORDER BY src, line, kind""",
        (var.id, *kinds),
    ).fetchall()
    fns = symbols.get_many(conn, sorted({r["src"] for r in rows}))
    per: dict[str, tuple[set[int], set[int], list[str]]] = {}
    for r in rows:
        if r["src"] == skip or r["src"] not in fns:
            continue
        if r["kind"] == "reads" and not_values and (r["src"], r["line"]) in not_values:
            continue
        reads, writes, levels = per.setdefault(r["src"], (set(), set(), []))
        (writes if r["kind"] == "writes" else reads).add(r["line"])
        levels.append(r["confidence"])
    out = []
    for fid, (reads, writes, confs) in per.items():
        worst = "ambiguous" if "ambiguous" in confs else confs[0]
        level, reason = certainty.of_access(worst, var.kind, question)
        if question == VALUE and var.kind != "field" and "[" in var.type and level == certainty.EXACT:
            level, reason = certainty.PROBABLE, "reads the same array, maybe another element"
        if not reads and not writes:
            continue
        out.append(Use(fns[fid], sorted(reads), sorted(writes), level, reason))
    return out


def _innermost(conn: sqlite3.Connection, file: str, line: int) -> Fn | None:
    r = conn.execute(
        """SELECT id FROM functions WHERE file = ? AND line_start <= ? AND line_end >= ?
           ORDER BY line_end - line_start LIMIT 1""",
        (file, line, line),
    ).fetchone()
    return symbols.get(conn, r[0]) if r else None


def global_at(conn: sqlite3.Connection, file: str, line: int) -> tuple[Var, str, str] | None:
    """The global whose initializer holds `file:line`, for a compile error outside every function (#57):
    (global, certainty, why). Exact when the index has proof: a row of that global's initializer on that
    line (a field it sets, an address it stores) or its definition starts there. Otherwise the nearest
    global defined above the line, probable. None when the file has no global above the line."""
    proof = conn.execute(
        """SELECT * FROM variables v WHERE v.kind = 'global' AND v.file = ? AND v.line <= ? AND (
               v.line = ?
               OR v.id IN (SELECT variable_id FROM initializers WHERE file = ? AND line = ?)
               OR v.id IN (SELECT function_id FROM escapes WHERE line = ?))
           ORDER BY v.line DESC, v.id LIMIT 1""",
        (file, line, line, file, line, line),
    ).fetchone()
    if proof is not None:
        return _row(proof), certainty.EXACT, "its initializer"
    near = conn.execute(
        """SELECT * FROM variables WHERE kind = 'global' AND file = ? AND line <= ?
           ORDER BY line DESC, id LIMIT 1""",
        (file, line),
    ).fetchone()
    if near is None:
        return None
    return _row(near), certainty.PROBABLE, "probably its initializer (the nearest global above that line)"


def compile_reason(reason: str) -> str:
    """'does not compile now: ...' once, whether or not `reason` already says it."""
    prefix = "does not compile now: "
    return reason if reason.startswith(prefix) else prefix + reason


def _container_names(var: Var) -> set[str]:
    """Names a compile error may use for the field's struct: its qualified container, and the short one."""
    if not var.container:
        return set()
    names = {var.container}
    parts = [p for p in var.container.split("::") if _location(p) is None]
    if parts:
        names |= {"::".join(parts), parts[-1]}
    return names


def _broken(conn: sqlite3.Connection, var: Var) -> list[Broken]:
    exact_names = _container_names(var)
    out = []
    for r in conn.execute(
        "SELECT file, line, member, record FROM missing_members WHERE member = ? ORDER BY file, line, record",
        (var.name,),
    ):
        fn = _innermost(conn, r["file"], r["line"])
        if r["record"] == "":
            # "use of undeclared identifier X": a removed global, or (C++) a removed member used without
            # this-> inside a method of its class
            if var.kind != "field":
                level = certainty.EXACT
                reason = f"does not compile now: use of undeclared identifier '{var.name}'"
            elif fn is not None and any(fn.qualified_name.startswith(n + "::") for n in exact_names):
                level = certainty.PROBABLE
                reason = (
                    f"use of undeclared identifier '{var.name}' inside a method of {var.record}: probably this "
                    "member, used without this->"
                )
            else:
                continue
        elif var.kind != "field":
            continue
        elif r["record"] in exact_names:
            level = certainty.EXACT
            reason = f"does not compile now: '{r['record']}' has no member named '{var.name}'"
        else:
            level = certainty.PROBABLE
            reason = (
                f"'{r['record']}' has no member named '{var.name}': the same field name in another type, which "
                "may be a typedef or a base of this one"
            )
        out.append(Broken(r["file"], r["line"], fn, r["record"], level, reason))
    out.sort(key=lambda b: (certainty.LEVELS.index(b.certainty), b.file, b.line, b.record))
    return out


def _initializers(conn: sqlite3.Connection, var: Var) -> list[InitSite]:
    rows = conn.execute(
        f"""SELECT i.file AS at_file, i.line AS at_line, {", ".join("v." + c for c in _VAR_COLS.split(", "))}
            FROM initializers i JOIN variables v ON v.id = i.variable_id
            WHERE i.field_id = ? ORDER BY i.file, i.line, v.id""",
        (var.id,),
    ).fetchall()
    seen = set()
    out = []
    for r in rows:
        key = (r["id"], r["at_file"], r["at_line"])
        if key in seen:
            continue
        seen.add(key)
        out.append(
            InitSite(
                _row(r),
                r["at_file"],
                r["at_line"],
                reason=f"sets it in the initializer of {r['qualified_name']}",
            )
        )
    return out


def _by_position(conn: sqlite3.Connection, var: Var) -> list[Var]:
    """Globals whose type is the field's struct (or an array of it). If their initializer lists values in
    order ({1, 2}), they may set this field by position: listed as unknown, because a value the parser can't
    match to a field for sure is left out of `initializers`."""
    names = _container_names(var)
    if not names:
        return []
    for _ in range(5):  # struct wrap { struct conn c; }: {{1, 2}, 3} sets conn's fields by position too
        outer = {
            n
            for r in conn.execute("SELECT type, container FROM variables WHERE kind = 'field' ORDER BY id")
            if r["container"] and _struct_name(r["type"]) in names and "*" not in r["type"]
            for n in _container_names(Var("", "", "", "", r["container"], "field", "", "", 0, False))
        }
        if outer <= names:
            break
        names |= outer
    out = []
    for r in conn.execute(
        f"SELECT {_VAR_COLS} FROM variables WHERE kind IN ('global', 'static_local') ORDER BY file, line, id"
    ):
        if _struct_name(r["type"]) in names and "*" not in r["type"]:
            out.append(_row(r))
    return out


def _escapes(conn: sqlite3.Connection, var: Var) -> list[Escape]:
    rows = conn.execute(
        "SELECT function_id, line, detail FROM escapes WHERE variable_id = ? ORDER BY function_id, line, detail",
        (var.id,),
    ).fetchall()
    fns = symbols.get_many(conn, sorted({r[0] for r in rows}))
    out = []
    for r in rows:
        reason = f"its address is {r[2].replace('address ', '', 1)}; what happens to it there is not seen"
        if r[0] in fns:
            out.append(Escape(fns[r[0]], r[1], r[2], reason=reason))
            continue
        # &x in a file-scope initializer (a table): the row is kept under that global (#27)
        g = conn.execute(f"SELECT {_VAR_COLS} FROM variables WHERE id = ?", (r[0],)).fetchone()
        if g is None:
            continue
        gv = _row(g)
        stand_in = Fn(gv.id, gv.name, gv.qualified_name, gv.file, gv.line, gv.line, gv.type, "", gv.clash)
        out.append(
            Escape(
                stand_in,
                r[1],
                r[2],
                reason=reason + " (code that uses the table can change it)",
                in_variable=gv,
            )
        )
    out.sort(key=lambda e: (e.fn.file, e.line, e.fn.id, e.detail))
    return out


def _struct_fields_written(conn: sqlite3.Connection, var: Var, writer: Fn) -> list[Var]:
    """Fields of a struct global's type that `writer` writes (server.hz = ... writes redisServer::hz)."""
    struct = _struct_name(var.type)
    rows = conn.execute(
        f"""SELECT DISTINCT {", ".join("v." + c for c in _VAR_COLS.split(", "))} FROM edges e
            JOIN variables v ON v.id = e.dst
            WHERE e.src = ? AND e.kind = 'writes' AND v.kind = 'field' AND v.container = ?
            ORDER BY v.qualified_name, v.id""",
        (writer.id, struct),
    ).fetchall()
    return [_row(r) for r in rows]


def data_users(
    conn: sqlite3.Connection, var_id: str, depth: int | None = None, writer_id: str | None = None
) -> DataUsers:
    """What a change to this variable affects. `var_id` is a `variables` id (Part C passes the id of a
    changed declaration). With `writer_id`, the value question: which functions read what that one writes."""
    r = conn.execute(f"SELECT {_VAR_COLS} FROM variables WHERE id = ?", (var_id,)).fetchone()
    if r is None:
        raise KeyError(var_id)
    display, _ = _names(conn, r)
    return data_users_of(conn, _row(r, display if display != r["qualified_name"] else ""), depth, writer_id)


def data_users_of(
    conn: sqlite3.Connection, var: Var, depth: int | None = None, writer_id: str | None = None
) -> DataUsers:
    """Same as `data_users`, for a Var you already have (a gone field has no id)."""
    if depth is None:
        depth = FIELD_DEPTH if var.kind == "field" else GLOBAL_DEPTH
    writer = symbols.get(conn, writer_id) if writer_id else None
    res = DataUsers(var, VALUE if writer else DECLARATION, writer, depth=depth)
    if var.kind == "field" and writer is None:
        res.by_position = _by_position(conn, var)  # also for a removed field: that is when values shift
    if var.exists:
        res.escapes = _escapes(conn, var)
        if writer is None:
            res.uses = _uses(conn, var, DECLARATION)
            if var.kind == "field":
                res.initializers = _initializers(conn, var)
            else:
                res.struct_fields, res.struct_fields_total = _struct_fields(conn, var)
        else:
            taken = {(e.fn.id, e.line) for e in res.escapes}
            writes_it = conn.execute(
                "SELECT 1 FROM edges WHERE src = ? AND dst = ? AND kind = 'writes'", (writer.id, var.id)
            ).fetchone()
            res.writer_writes = bool(writes_it)
            if writes_it:
                res.uses = _uses(conn, var, VALUE, skip=writer.id, kinds=("reads",), not_values=taken)
            elif var.kind != "field":
                # a struct global (server): the writer writes its fields (server.hz = ...), not the global
                res.via_fields = _struct_fields_written(conn, var, writer)
                merged: dict[str, Use] = {}
                for f in res.via_fields:
                    f_escapes = _escapes(conn, f)
                    res.escapes += f_escapes
                    f_taken = {(e.fn.id, e.line) for e in f_escapes}
                    for u in _uses(conn, f, VALUE, skip=writer.id, kinds=("reads",), not_values=f_taken):
                        u.via_field = f.shown
                        cur = merged.get(u.fn.id)
                        if cur is None:
                            merged[u.fn.id] = u
                        else:
                            cur.reads = sorted(set(cur.reads) | set(u.reads))
                            cur.via_field = ", ".join(sorted({*cur.via_field.split(", "), u.via_field}))
                res.uses = list(merged.values())
                res.escapes.sort(key=lambda e: (e.fn.file, e.line, e.fn.id, e.detail))
        res.uses.sort(key=_rank)
        same_usr = conn.execute(
            f"SELECT {_VAR_COLS} FROM variables WHERE usr = ? AND id != ? ORDER BY file, line, id",
            (var.usr, var.id),
        ).fetchall()
        res.other_copies = [_row(x) for x in same_usr]
    if writer is None and (var.kind == "field" or not var.exists):
        res.broken = _broken(conn, var)
    copies = {v.id for v in res.other_copies} | {var.id}
    res.same_name = [v for v, _ in _by_name(conn, var.name) if v.id not in copies]
    starts = [u.fn for u in res.uses] + [b.fn for b in res.broken if b.fn is not None]
    if starts and depth > 0:
        res.upstream, res.truncated = graph.walk_many(
            conn, list({f.id: f for f in starts}.values()), "callers", depth
        )
    res.blind_spots = _limits(conn, res)
    return res


# ---------------------------------------------------------------- limits of the answer


def _has_cpp(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute(
            """SELECT 1 FROM files WHERE path LIKE '%.cpp' OR path LIKE '%.cc' OR path LIKE '%.cxx'
               OR path LIKE '%.hpp' OR path LIKE '%.hh' LIMIT 1"""
        ).fetchone()
        is not None
    )


def _limits(conn: sqlite3.Connection, res: DataUsers) -> list[str]:
    v = res.target
    notes: list[str] = []
    is_field = v.kind == "field"
    record = v.record or "?"
    if is_field and res.question == DECLARATION:
        notes.append(
            f"Fields are tracked per struct type, not per object: every {record}'s {v.name} is listed."
        )
    if res.question == VALUE:
        if is_field or res.via_fields:
            notes.append(
                "Fields are tracked per struct type, not per object: these functions may read another object "
                "of the same type, so they are only probable."
            )
        else:
            notes.append(
                "A plain global is one object, so its readers are exact. Whether a reader runs before or after "
                "the write is not known."
            )
        if not res.writer_writes and not res.via_fields and res.writer is not None:
            notes.append(
                f"{res.writer.qualified_name} does not write {v.shown} directly (nor a field of it). "
                f"Ask without a writer to see every user of {v.shown}."
            )
    if not v.exists:
        if is_field:
            notes.append(
                f"{v.shown} is not declared any more. Listed: places that fail to compile because of it "
                '("no member named", "field designator ... does not refer to any field", and in C++ "use of '
                'undeclared identifier" inside a method of its class, probable).'
            )
        else:
            notes.append(
                f'{v.shown} is not declared any more. Listed: places that fail with "use of undeclared '
                'identifier". A file that still declares it (an extern left in a header) compiles, and only the '
                "link fails: those uses are not listed."
            )
    if res.broken or not v.exists:
        notes.append(
            "Several uses on one line (for example two calls of the same macro) count as one place. Inside a "
            "macro, the error is at the line where the macro is used."
        )
    if res.question == VALUE and res.escapes:
        notes.append(
            "Places that take its address (&x) are listed as unknown, not as readers: code that reads it later "
            "through that pointer is not seen."
        )
    if res.escapes:
        notes.append(
            f"{len(res.escapes)} place{'s pass' if len(res.escapes) != 1 else ' passes'} its address on. Reads and "
            "writes made through that pointer elsewhere are not listed."
        )
    notes.append(
        "Not seen: access through a void * or a cast to another type, offsetof outside a function (a "
        "file-scope table or static_assert), or by size and layout (sizeof, copying the whole struct, raw reads "
        "and writes of its bytes)."
    )
    if is_field:
        notes.append(
            "Initializers that set it by position ({1, 2}) are listed. A value AeroTrace can't match to a field "
            "for sure (after a designator, as in {.a = 1, 2}) is left out, so globals of this struct type, or of "
            "a struct that contains it, are still listed as unknown. Not checked there: globals declared with a "
            "typedef name other than the struct's (node_t for struct node), and local variables. {0} (all "
            "zero) is not counted."
        )
    if v.in_union:
        notes.append(
            "It is a member of a union: writing another member of the same union changes it too, and that is "
            "not listed."
        )
    if v.kind == "global" and not v.is_static:
        notes.append(
            f"{v.name} can be used from outside this repo (code that links to it); that is not seen."
        )
    if _has_cpp(conn):
        notes.append(
            "C++: reads in a file-scope initializer (int b = a + 1;) are not recorded (#28), and member access "
            "through a type that depends on a template parameter is not linked."
        )
    notes.append(
        "Inside a macro, the line is where the macro is used. Names built by a macro with ## are only seen "
        "where that macro is expanded in this build."
    )
    if res.other_copies:
        o = res.other_copies
        notes.append(
            f"The same {'struct' if is_field else 'name'} is also defined in "
            + ", ".join(f"{c.file}:{c.line}" for c in o[:3])
            + f". Changing one copy does not change the other; ask for {v.shown}@{o[0].file} too if both "
            "must stay the same."
        )
    if res.truncated:
        notes.append(f"Stopped at depth {res.depth}; more callers exist further up.")
    return notes + graph.index_limits(conn)
