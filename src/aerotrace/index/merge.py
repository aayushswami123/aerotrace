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
    address_taken: list[str] = field(default_factory=list)  # function ids
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
    taken: set[str] = set()

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
        for fn, ffile, line in res.get("pointer_calls", []):
            if fn in fns.defs:
                blind.add(
                    (fns.id_of(fn, ffile), "pointer_call", line, "call through a pointer; target unknown")
                )
        for usr, base in res.get("overrides", []):
            for a in fns.ids(usr):
                for b in fns.ids(base):
                    overrides.add((a, b))
        for usr in res.get("address_taken", []):
            taken.update(fns.ids(usr))
        for fn, ffile, n in res.get("external_calls", []):
            if fn in fns.defs:
                external[fns.id_of(fn, ffile)] += n

    for a, b in overrides:
        edges.add((a, b, "overrides", "exact", 0))

    # A virtual call through a pointer or reference may land on any override of the method it names.
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
    graph.address_taken = sorted(taken)
    return graph
