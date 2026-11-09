"""Text and JSON for `aerotrace data` / the `data_users` MCP tool. Summary first, ranked, capped lists that
say what was left out (the conventions of docs/plans/ranking.md)."""

from __future__ import annotations

from collections import Counter

from aerotrace.analysis import certainty
from aerotrace.analysis.data import VALUE, DataUsers, Use, Var
from aerotrace.analysis.graph import CONFIDENCE_MEANING, CONFIDENCE_ORDER, Hit

USE_CAP = 15
LINES_PER_USE = 5
INIT_CAP = 10
POSITION_CAP = 10
ESCAPE_CAP = 10
UPSTREAM_CAP = 15
FILES_CAP = 5
SAME_NAME_CAP = 5
JSON_CAP = 50
CLI_MORE = "use --all to see every one, or --limit N"
MCP_MORE = "call again with show_all=true, or a bigger limit"


def _n(n: int, word: str, plural: str | None = None) -> str:
    return f"{n} {word if n == 1 else (plural or word + 's')}"


def _more(n: int, what: str, hint: str) -> str:
    return f"  ... and {n} more {what} ({hint})."


def _what(v: Var) -> str:
    if not v.exists:
        return f"a field of {v.record} that is not declared any more (code still uses it)"
    if v.kind == "field":
        return f"field of {v.record}, type {v.type}"
    if v.kind == "static_local":
        return f"static local variable, type {v.type}"
    scope = "static global (this file only)" if v.is_static else "global"
    return f"{scope}, type {v.type}"


def _lines(u: Use, cap: int | None) -> str:
    """'writes 1572, 1579, 1581; reads 1576, 1580 (+15 more lines)'."""
    total = len(u.writes) + len(u.reads)
    budget = total if cap is None else cap
    parts = []
    for word, ls in (("writes", u.writes), ("reads", u.reads)):
        take = ls[:budget]
        if take:
            parts.append(word + " " + ", ".join(str(x) for x in take))
            budget -= len(take)
    rest = total - (total if cap is None else min(cap, total))
    return "; ".join(parts) + (f" (+{_n(rest, 'more line')})" if rest else "")


def _use_line(u: Use, cap: int | None) -> str:
    tag = "" if u.certainty == certainty.EXACT else f" [{u.certainty}]"
    via = f" ({u.via_field})" if u.via_field else ""
    return f"  - {u.fn.qualified_name}  {u.fn.file}  first use {u.first}{tag}: {_lines(u, cap)}{via}"


def _hit_line(h: Hit) -> str:
    tag = "" if h.confidence == "exact" else f" [{h.confidence}]"
    via = f" via {h.via}" if h.via else ""
    return f"    - {h.fn.qualified_name}  {h.fn.where}{tag}{via}"


def data_text(r: DataUsers, limit: int | None = None, show_all: bool = False, more: str = CLI_MORE) -> str:
    def cap(default: int) -> int | None:
        return None if show_all else (limit if limit is not None else default)

    v = r.target
    head = f"{v.shown} ({v.where})" if v.exists else v.shown
    lines: list[str] = []
    if r.question == VALUE and r.writer is not None:
        w = r.writer
        lines.append(f"Who reads what {w.qualified_name} ({w.where}) writes to {head}: {_what(v)}.")
        if r.via_fields:
            names = ", ".join(f.name for f in r.via_fields[:10])
            lines.append(
                f"{w.qualified_name} writes {_n(len(r.via_fields), 'field')} of {v.shown} ({names}); "
                "the readers of those fields are listed."
            )
        level = Counter(u.certainty for u in r.uses)
        lines.append(
            f"Summary: {_n(len(r.uses), 'other function')} {'reads' if len(r.uses) == 1 else 'read'} it"
            + (
                " (" + ", ".join(f"{level[lv]} {lv}" for lv in certainty.LEVELS if level[lv]) + ")"
                if r.uses
                else ""
            )
            + "."
            + (
                f" {_n(len(r.upstream), 'caller')} further up ({r.depth} step{'s' if r.depth != 1 else ''})."
                if r.depth > 0
                else ""
            )
        )
    else:
        lines.append(f"Data users of {head}: {_what(v)}.")
        n_w = len(r.writers)
        level = Counter(u.certainty for u in r.uses)
        files = r.files()
        lv = ", ".join(f"{level[x]} {x}" for x in certainty.LEVELS if level[x])
        summary = (
            f"Summary: {_n(len(r.uses), 'function')} {'uses' if len(r.uses) == 1 else 'use'} it"
            + (f" in {_n(len(files), 'file')}" if r.uses else "")
            + (
                f": {n_w} {'writes' if n_w == 1 else 'write'} it, {len(r.uses) - n_w} only "
                f"{'reads' if len(r.uses) - n_w == 1 else 'read'} it ({lv})"
                if r.uses
                else ""
            )
            + "."
        )
        extra = []
        if v.kind == "field":
            n_init = len({i.variable.id for i in r.initializers})
            extra.append(f"{_n(n_init, 'initializer')} {'sets' if n_init == 1 else 'set'} it")
            extra.append(f"{_n(len(r.by_position), 'global')} of its type may set it by position")
        extra.append(
            f"{_n(len(r.escapes), 'place')} {'passes' if len(r.escapes) == 1 else 'pass'} its address on"
        )
        if v.kind == "field":
            extra.append(
                f"{_n(len(r.broken), 'place')} {'does' if len(r.broken) == 1 else 'do'} not compile now"
            )
        if r.depth > 0:
            extra.append(
                f"{_n(len(r.upstream), 'caller')} further up ({r.depth} step{'s' if r.depth != 1 else ''})"
            )
        lines.append(summary + " " + ", ".join(extra)[0].upper() + ", ".join(extra)[1:] + ".")
        if len(files) > 1:
            fc = cap(FILES_CAP)
            lines.append(
                "Most-used files: "
                + ", ".join(f"{f} ({n})" for f, n in (files if fc is None else files[:fc]))
                + "."
            )
    if r.broken:
        lines.append(f"Places that do not compile now ({len(r.broken)}):")
        for b in r.broken:
            where = f"{b.fn.qualified_name}  " if b.fn else ""
            tag = "" if b.certainty == certainty.EXACT else f" [{b.certainty}]"
            lines.append(f"  - {where}{b.file}:{b.line}{tag}: {b.reason}")
    if r.uses:
        c = cap(USE_CAP)
        shown = r.uses if c is None else r.uses[:c]
        order = "" if r.question == VALUE else ", writers first"
        first = "" if len(shown) == len(r.uses) else f", first {len(shown)}"
        title = "Functions that read it" if r.question == VALUE else "Functions that use it"
        lines.append(f"{title} ({len(r.uses)}){order}{first}:")
        lc = None if show_all else LINES_PER_USE
        lines += [_use_line(u, lc) for u in shown]
        if len(shown) < len(r.uses):
            lines.append(_more(len(r.uses) - len(shown), "functions", more))
    if r.initializers:
        per: dict[str, list] = {}
        for i in r.initializers:
            per.setdefault(i.variable.id, []).append(i)
        lines.append(
            f"Initializers that set it ({_n(len(r.initializers), 'line')} in {_n(len(per), 'global')}):"
        )
        c = cap(INIT_CAP)
        groups = list(per.values())
        for g in groups if c is None else groups[:c]:
            ls = [i.line for i in g]
            lc = None if show_all else LINES_PER_USE
            shown_ls = ls if lc is None else ls[:lc]
            rest = len(ls) - len(shown_ls)
            lines.append(
                f"  - {g[0].variable.shown}  {g[0].file}: "
                + ("line " if len(ls) == 1 else "lines ")
                + ", ".join(str(x) for x in shown_ls)
                + (f" (+{_n(rest, 'more line')})" if rest else "")
            )
        if c is not None and len(groups) > c:
            lines.append(_more(len(groups) - c, "globals", more))
    if r.by_position:
        c = cap(POSITION_CAP)
        lines.append(
            f"Globals of its type that may set it by position ({len(r.by_position)}) [unknown]: "
            "if their initializer lists values in order, it sets this field too:"
        )
        for g in r.by_position if c is None else r.by_position[:c]:
            lines.append(f"  - {g.shown}  {g.file}:{g.line}  ({g.type})")
        if c is not None and len(r.by_position) > c:
            lines.append(_more(len(r.by_position) - c, "globals", more))
    if r.escapes:
        c = cap(ESCAPE_CAP)
        lines.append(f"Places its address goes where AeroTrace can't follow it ({len(r.escapes)}) [unknown]:")
        for e in r.escapes if c is None else r.escapes[:c]:
            lines.append(f"  - {e.fn.qualified_name}  {e.fn.file}:{e.line}: {e.detail}")
        if c is not None and len(r.escapes) > c:
            lines.append(_more(len(r.escapes) - c, "places", more))
    if r.upstream:
        c = cap(UPSTREAM_CAP)
        lines.append(
            f"Callers further up ({len(r.upstream)}, {r.depth} step{'s' if r.depth != 1 else ''}), nearest first:"
        )
        shown_hits = r.upstream if c is None else r.upstream[:c]
        by_d: dict[int, list[Hit]] = {}
        for h in shown_hits:
            by_d.setdefault(h.distance, []).append(h)
        totals = Counter(h.distance for h in r.upstream)
        for d in sorted(by_d):
            lines.append(f"  Distance {d} ({totals[d]}):")
            lines += [_hit_line(h) for h in by_d[d]]
        if len(shown_hits) < len(r.upstream):
            lines.append(_more(len(r.upstream) - len(shown_hits), "callers", more))
        used = sorted({h.confidence for h in r.upstream} - {"exact"}, key=CONFIDENCE_ORDER.index)
        lines += [f"[{x}] = {CONFIDENCE_MEANING[x]}" for x in used]
    if r.same_name:
        c = SAME_NAME_CAP
        kinds = {x.kind for x in r.same_name}
        word = "field" if kinds == {"field"} else "variable"
        names = ", ".join(x.shown + (f"@{x.file}" if x.kind != "field" else "") for x in r.same_name[:c])
        left = len(r.same_name) - min(c, len(r.same_name))
        lines.append(
            f"Not included: {_n(len(r.same_name), 'other ' + word)} named {v.name} ({names}"
            + (f", and {left} more" if left else "")
            + "). Ask for one by its full name."
        )
    levels = {u.certainty for u in r.uses} | {b.certainty for b in r.broken}
    if r.escapes or r.by_position:
        levels.add(certainty.UNKNOWN)
    lines += [
        f"[{x}] = {certainty.MEANING[x]}" for x in certainty.LEVELS if x in levels and x != certainty.EXACT
    ]
    probable = sorted({u.reason for u in r.uses if u.certainty == certainty.PROBABLE})
    lines += [f"  probable because: {p}" for p in probable[:3]]
    if r.blind_spots:
        lines.append("Limits of this answer:")
        lines += [f"  - {n}" for n in r.blind_spots]
    return "\n".join(lines)


def _use_json(u: Use) -> dict:
    return {
        "function": u.fn.brief(),
        "first_use": u.first,
        "writes": u.writes,
        "reads": u.reads,
        "certainty": u.certainty,
        "reason": u.reason,
        **({"via_field": u.via_field} if u.via_field else {}),
    }


def data_json(r: DataUsers, limit: int | None = None, show_all: bool = False) -> dict:
    n = None if show_all else (JSON_CAP if limit is None else limit)

    def head(items: list) -> list:
        return items if n is None else items[:n]

    def left(items: list) -> int:
        return 0 if n is None else max(0, len(items) - n)

    level = Counter(u.certainty for u in r.uses)
    return {
        "target": r.target.brief(),
        "question": r.question,
        "writer": r.writer.brief() if r.writer else None,
        "via_fields": [f.shown for f in r.via_fields],
        "summary": {
            "functions": len(r.uses),
            "writers": len(r.writers),
            "files": len(r.files()),
            "by_certainty": {x: level[x] for x in certainty.LEVELS},
            "initializers": len(r.initializers),
            "may_set_by_position": len(r.by_position),
            "escapes": len(r.escapes),
            "not_compiling": len(r.broken),
            "upstream": len(r.upstream),
            "functions_per_file": [{"file": f, "count": c} for f, c in r.files()],
        },
        "not_compiling": [
            {
                "file": b.file,
                "line": b.line,
                "function": b.fn.brief() if b.fn else None,
                "record": b.record,
                "certainty": b.certainty,
                "reason": b.reason,
            }
            for b in r.broken
        ],
        "uses": [_use_json(u) for u in head(r.uses)],
        "uses_total": len(r.uses),
        "uses_omitted": left(r.uses),
        "initializers": [
            {
                "variable": i.variable.shown,
                "file": i.file,
                "line": i.line,
                "certainty": i.certainty,
                "reason": i.reason,
            }
            for i in head(r.initializers)
        ],
        "initializers_total": len(r.initializers),
        "initializers_omitted": left(r.initializers),
        "may_set_by_position": [
            {
                **g.brief(),
                "certainty": certainty.UNKNOWN,
                "reason": "a global of this struct type; an initializer that lists values in order sets it",
            }
            for g in head(r.by_position)
        ],
        "may_set_by_position_total": len(r.by_position),
        "escapes": [
            {
                "function": e.fn.brief(),
                "line": e.line,
                "detail": e.detail,
                "certainty": e.certainty,
                "reason": e.reason,
            }
            for e in head(r.escapes)
        ],
        "escapes_total": len(r.escapes),
        "escapes_omitted": left(r.escapes),
        "upstream": [
            {
                **h.fn.brief(),
                "distance": h.distance,
                "confidence": h.confidence,
                "via": h.via,
                "lines": h.lines,
            }
            for h in head(r.upstream)
        ],
        "upstream_total": len(r.upstream),
        "upstream_omitted": left(r.upstream),
        "truncated_at_depth": r.truncated,
        "same_name": [{"name": x.shown, "kind": x.kind, "file": x.file, "line": x.line} for x in r.same_name],
        "other_copies": [{"name": x.shown, "file": x.file, "line": x.line} for x in r.other_copies],
        "blind_spots": r.blind_spots,
    }


def candidates_text(query: str, found: list[Var], limit: int = 20) -> str:
    if not found:
        return f"No field or global named '{query}' in the index."
    names = Counter(v.shown for v in found)
    rows = [
        f"  - {v.shown}{'@' + v.file if names[v.shown] > 1 and v.exists else ''}  "
        f"({v.kind}{', ' + v.type if v.type else ''}, {v.where})"
        for v in found[:limit]
    ]
    more = [f"  ... and {len(found) - limit} more."] if len(found) > limit else []
    return "\n".join(
        [f"'{query}' matches {_n(len(found), 'variable')}. Ask again with one of these:", *rows, *more]
    )
