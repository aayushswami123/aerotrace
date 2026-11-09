"""Merge per-file facts into one graph, with collision-safe IDs.

ID rules (docs/decisions/0004-ids-and-confidence.md):
- A symbol's ID is its clang USR.
- If one USR is defined in more than one file (five `main()`s, two `static` helpers in two files that are
  both named `util.c`), each definition gets `<usr>@<file>` instead, and is marked as a clash.
- A reference to a clashing USR goes to the definition the translation unit can see (its own file or a
  header it includes). If it can't see exactly one, it goes to every definition, marked `ambiguous`.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class Graph:
    functions: list[dict] = field(default_factory=list)
    variables: list[dict] = field(default_factory=list)
    edges: list[tuple] = field(default_factory=list)  # (src, dst, kind, confidence, line)
    blind_spots: list[tuple] = field(default_factory=list)  # (function_id, kind, line, detail)
    address_taken: list[tuple[str, str]] = field(default_factory=list)  # (function id, function type)
    # (caller, callee or None, confidence, line, column, n_args, use, detail, pointer type or "")
    call_sites: list[tuple] = field(default_factory=list)
    # (function id, in function id or None, in variable id or None, file, line, how)
    address_sites: list[tuple] = field(default_factory=list)
    escapes: list[tuple] = field(default_factory=list)  # (function id, variable id, line, detail)
    initializers: list[tuple] = field(default_factory=list)  # (global id, field id, file, line)
    missing_members: list[tuple] = field(default_factory=list)  # (file, line, member, record)
    class_bases: dict[str, list[str]] = field(default_factory=dict)


class _Symbols:
    """Definitions of one kind (functions or variables), keyed by USR then file."""

    def __init__(self) -> None:
        self.defs: dict[str, dict[str, dict]] = defaultdict(dict)

    def ids(self, usr: str) -> list[str]:
        files = self.defs.get(usr, {})
        if len(files) == 1:
            return [usr]
        return [f"{usr}@{f}" for f in sorted(files)]

    def id_of(self, usr: str, file: str) -> str:
        return usr if len(self.defs[usr]) == 1 else f"{usr}@{file}"

    def resolve(self, usr: str, visible_files: set[str]) -> list[tuple[str, str]]:
        """[(id, confidence)] for a reference to `usr` from a TU that can see `visible_files`."""
        files = self.defs.get(usr)
        if not files:
            return []
        if len(files) == 1:
            return [(usr, "exact")]
        seen = sorted(f for f in files if f in visible_files)
        if len(seen) == 1:
            return [(f"{usr}@{seen[0]}", "exact")]
        return [(f"{usr}@{f}", "ambiguous") for f in (seen or sorted(files))]


def merge(results: list[dict]) -> Graph:
    """`results` are parse_unit() outputs. They must be in a fixed order (sorted by file) for determinism."""
    fns = _Symbols()
    vars_ = _Symbols()
    class_bases: dict[str, list[str]] = {}
    external: dict[str, int] = defaultdict(int)

    for res in results:
        for rec in res.get("functions", []):
            cur = fns.defs[rec["usr"]].get(rec["file"])
            if cur is None or (cur["is_pure_virtual"] and not rec["is_pure_virtual"]):
                fns.defs[rec["usr"]][rec["file"]] = rec
        for rec in res.get("variables", []):
            cur = vars_.defs[rec["usr"]].get(rec["file"])
            if cur is None or (rec["is_definition"] and not cur["is_definition"]):
                vars_.defs[rec["usr"]][rec["file"]] = rec
        for cls, bases in res.get("class_bases", {}).items():
            known = class_bases.setdefault(cls, [])
            known.extend(b for b in bases if b not in known)

    # A pure-virtual declaration is not a second definition when a real body exists somewhere.
    for files in fns.defs.values():
        if len(files) > 1 and any(not r["is_pure_virtual"] for r in files.values()):
            for f in [f for f, r in files.items() if r["is_pure_virtual"]]:
                del files[f]
    # `extern int g;` in a header is not a second definition of `int g;` in a .c file.
    for files in vars_.defs.values():
        if len(files) > 1 and any(r["is_definition"] for r in files.values()):
            for f in [f for f, r in files.items() if not r["is_definition"]]:
                del files[f]

    edges: set[tuple] = set()
    dispatch_calls: set[tuple] = set()  # (src, static target id, line)
    blind: set[tuple] = set()
    overrides: set[tuple[str, str]] = set()
    taken: dict[str, str] = {}  # function id -> type
    pointer_sites: set[tuple] = set()  # (src, line, called type)
    call_sites: set[tuple] = set()
    address_sites: set[tuple] = set()
    escapes: set[tuple] = set()
    initializers: set[tuple] = set()
    missing_members: set[tuple] = set()

    for res in results:
        visible = set(res.get("deps", {}))
        for caller, cfile, callee, line, dispatch, name in res.get("calls", []):
            if caller not in fns.defs:
                continue
            src = fns.id_of(caller, cfile)
            targets = fns.resolve(callee, visible)
            if not targets:
                blind.add(
                    (src, "unresolved_call", line, f"{name}: declared in the repo, no definition was parsed")
                )
                continue
            for dst, conf in targets:
                edges.add((src, dst, "calls", conf, line))
                if dispatch:
                    dispatch_calls.add((src, dst, line))
        for fn, ffile, var, kind, line in res.get("accesses", []):
            if fn not in fns.defs:
                continue
            src = fns.id_of(fn, ffile)
            for dst, conf in vars_.resolve(var, visible):
                edges.add((src, dst, kind, conf, line))
        for fn, ffile, line, ftype in res.get("pointer_calls", []):
            if fn in fns.defs:
                pointer_sites.add((fns.id_of(fn, ffile), line, ftype))
        for usr, base in res.get("overrides", []):
            for a in fns.ids(usr):
                for b in fns.ids(base):
                    overrides.add((a, b))
        for usr, ftype in res.get("address_taken", []):
            for fid in fns.ids(usr):
                taken.setdefault(fid, ftype)
        for fn, ffile, n in res.get("external_calls", []):
            if fn in fns.defs:
                external[fns.id_of(fn, ffile)] += n
        for caller, cfile, callee, _name, line, col, n_args, use, detail, ptype in res.get("call_sites", []):
            if caller not in fns.defs:
                continue
            src = fns.id_of(caller, cfile)
            if not callee:
                call_sites.add((src, None, "via_pointer", line, col, n_args, use, detail, ptype))
                continue
            for dst, conf in fns.resolve(callee, visible):
                call_sites.add((src, dst, conf, line, col, n_args, use, detail, ""))
        for fn, in_fn, in_file, in_var, file, line, how in res.get("address_sites", []):
            in_fn_id = fns.id_of(in_fn, in_file) if in_fn and in_fn in fns.defs else None
            in_var_ids = [v for v, _ in vars_.resolve(in_var, visible)] if in_var else []
            for dst, _conf in fns.resolve(fn, visible):
                for v in in_var_ids or [None]:
                    address_sites.add((dst, in_fn_id, v, file, line, how))
        for fn, ffile, var, line, detail in res.get("escapes", []):
            if fn not in fns.defs:
                continue
            for dst, _conf in vars_.resolve(var, visible):
                escapes.add((fns.id_of(fn, ffile), dst, line, detail))
        for gvar, gfile, fld, file, line in res.get("initializers", []):
            if gvar not in vars_.defs or gfile not in vars_.defs[gvar]:
                continue
            for dst, _conf in vars_.resolve(fld, visible):
                initializers.add((vars_.id_of(gvar, gfile), dst, file, line))
        for row in res.get("missing_members", []):
            missing_members.add(tuple(row))

    for a, b in overrides:
        edges.add((a, b, "overrides", "exact", 0))

    # Function-pointer pass: a call through a pointer of type T may reach any function of type T whose
    # address is taken somewhere (stored in a table, passed as a callback). Labelled `via_pointer`, and
    # the call site stays a blind spot, because the real target is still not proven.
    by_type: dict[str, list[str]] = defaultdict(list)
    for fid, ftype in sorted(taken.items()):
        if ftype:
            by_type[ftype].append(fid)
    for src, line, ftype in sorted(pointer_sites):
        candidates = [c for c in by_type.get(ftype, []) if c != src] if ftype else []
        for dst in candidates:
            edges.add((src, dst, "calls", "via_pointer", line))
        if not ftype:
            detail = "call through a pointer or dependent name; target unknown"
        elif candidates:
            detail = (
                f"call through a pointer to '{ftype}'; may reach {len(candidates)} functions of that type"
            )
        else:
            detail = f"call through a pointer to '{ftype}'; no function of that type has its address taken"
        blind.add((src, "pointer_call", line, detail))

    # A virtual call through a pointer or reference may land on any override of the method it names.
    sites_at: dict[tuple, list[tuple]] = defaultdict(list)
    for row in call_sites:
        if row[1] is not None:
            sites_at[(row[0], row[1], row[3])].append(row)
    overridden_by: dict[str, set[str]] = defaultdict(set)
    for a, b in overrides:
        overridden_by[b].add(a)
    for src, dst, line in sorted(dispatch_calls):
        todo, seen = [dst], set()
        while todo:
            for o in sorted(overridden_by.get(todo.pop(), ())):
                if o not in seen:
                    seen.add(o)
                    todo.append(o)
        for o in sorted(seen):
            if o != src:
                edges.add((src, o, "calls", "virtual", line))
                for row in sites_at.get((src, dst, line), []):
                    call_sites.add((src, o, "virtual", *row[3:]))

    graph = Graph(class_bases=dict(sorted(class_bases.items())))
    for usr, files in sorted(fns.defs.items()):
        for f, rec in sorted(files.items()):
            fid = fns.id_of(usr, f)
            graph.functions.append(
                {**rec, "id": fid, "clash": len(files) > 1, "external_calls": external[fid]}
            )
    for usr, files in sorted(vars_.defs.items()):
        for f, rec in sorted(files.items()):
            graph.variables.append({**rec, "id": vars_.id_of(usr, f), "clash": len(files) > 1})
    graph.edges = sorted(edges)
    graph.blind_spots = sorted(blind)
    graph.address_taken = sorted(taken.items())
    key = lambda t: tuple("" if x is None else x for x in t)  # noqa: E731 (sort rows that hold None)
    graph.call_sites = sorted(call_sites, key=key)
    graph.address_sites = sorted(address_sites, key=key)
    graph.escapes = sorted(escapes)
    graph.initializers = sorted(initializers)
    graph.missing_members = sorted(missing_members)
    return graph
