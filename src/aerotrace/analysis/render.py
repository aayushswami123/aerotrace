"""Short, ranked, plain-English answers for agents and people. Lists are capped and say what was left out."""

from __future__ import annotations

from collections import Counter

from aerotrace.analysis import certainty
from aerotrace.analysis.diff import DiffImpact, ranges
from aerotrace.analysis.graph import CONFIDENCE_MEANING, CONFIDENCE_ORDER, Hit, Impact, Walk
from aerotrace.analysis.signature import USE_WORDS, USES, SignatureImpact, Site
from aerotrace.analysis.symbols import Fn


def _hit_line(h: Hit, show_via: bool) -> str:
    via = f" via {h.via}" if show_via and h.via else ""
    tag = "" if h.confidence == "exact" else f" [{h.confidence}]"
    return f"  - {h.fn.qualified_name}  {h.fn.where}{tag}{via}"


def _levels(hits: list[Hit], limit: int, via_always: bool = False) -> tuple[list[str], list[Hit]]:
    """Lines grouped by distance, at most `limit` functions, and the hits that were shown."""
    out: list[str] = []
    shown_hits: list[Hit] = []
    shown = 0
    by_distance: dict[int, list[Hit]] = {}
    for h in hits:
        by_distance.setdefault(h.distance, []).append(h)
    for d in sorted(by_distance):
        group = by_distance[d]
        if shown >= limit:
            break
        out.append(f"Distance {d} ({len(group)}):")
        for h in group[: max(0, limit - shown)]:
            out.append(_hit_line(h, show_via=via_always or d > 1))
            shown_hits.append(h)
            shown += 1
    if len(hits) > shown:
        out.append(f"... and {len(hits) - shown} more (raise `limit` to see them).")
    return out, shown_hits


def _legend(hits: list[Hit]) -> list[str]:
    used = sorted({h.confidence for h in hits} - {"exact"}, key=CONFIDENCE_ORDER.index)
    return [f"[{c}] = {CONFIDENCE_MEANING[c]}" for c in used]


def walk_text(w: Walk, limit: int = 50) -> str:
    what = "Callers of" if w.direction == "callers" else "Functions called by"
    lines = [f"{what} {w.start.qualified_name} ({w.start.where}), depth {w.depth}: {len(w.hits)} found."]
    if not w.hits:
        lines.append("None found in the indexed code.")
    level_lines, shown = _levels(w.hits, limit)
    lines += level_lines
    if w.truncated_at_depth:
        lines.append(f"More exist beyond depth {w.depth}.")
    lines += _legend(shown)
    return "\n".join(lines)


def impact_text(imp: Impact, limit: int = 50) -> str:
    hits = imp.callers.hits
    counts = Counter(h.confidence for h in hits)
    parts = [f"{counts[c]} {c}" for c in CONFIDENCE_ORDER if counts[c]]
    t = imp.target
    lines = [
        f"Changing {t.qualified_name} ({t.where}) may affect {len(hits)} functions"
        + (f" ({', '.join(parts)})." if parts else ".")
    ]
    if imp.writes:
        lines.append(
            f"It writes: {', '.join(imp.writes[:limit])}" + (" ..." if len(imp.writes) > limit else "")
        )
    if imp.data_readers:
        lines.append("Functions that read that data:")
        for name, vars_ in list(imp.data_readers.items())[:limit]:
            lines.append(f"  - {name} reads {', '.join(vars_)}")
        if len(imp.data_readers) > limit:
            lines.append(f"  ... and {len(imp.data_readers) - limit} more.")
    shown: list[Hit] = []
    if hits:
        lines.append("Affected callers, nearest first:")
        level_lines, shown = _levels(hits, limit)
        lines += level_lines
    lines += _legend(shown)
    if imp.blind_spots:
        lines.append("Limits of this answer:")
        lines += [f"  - {n}" for n in imp.blind_spots]
    return "\n".join(lines)


def candidates_text(query: str, fns: list[Fn], limit: int = 20) -> str:
    if not fns:
        return f"No function matches '{query}'. Try `find` with part of the name, or `file.c:LINE`."
    lines = [f"'{query}' matches {len(fns)} functions. Ask again with one of these:"]
    lines += [f"  - {f.qualified_name}@{f.file}  ({f.where})" for f in fns[:limit]]
    if len(fns) > limit:
        lines.append(f"  ... and {len(fns) - limit} more.")
    return "\n".join(lines)


def hit_json(h: Hit) -> dict:
    return {
        **h.fn.brief(),
        "distance": h.distance,
        "confidence": h.confidence,
        "via": h.via,
        "lines": h.lines,
    }


def walk_json(w: Walk, limit: int = 50) -> dict:
    return {
        "start": w.start.brief(),
        "direction": w.direction,
        "depth": w.depth,
        "total": len(w.hits),
        "truncated_at_depth": w.truncated_at_depth,
        "results": [hit_json(h) for h in w.hits[:limit]],
        "omitted": max(0, len(w.hits) - limit),
    }


def impact_json(imp: Impact, limit: int = 50) -> dict:
    counts = Counter(h.confidence for h in imp.callers.hits)
    return {
        "target": imp.target.brief(),
        "total": len(imp.callers.hits),
        "by_confidence": {c: counts[c] for c in CONFIDENCE_ORDER if counts[c]},
        "writes": imp.writes,
        "data_readers": imp.data_readers,
        "affected": [hit_json(h) for h in imp.callers.hits[:limit]],
        "omitted": max(0, len(imp.callers.hits) - limit),
        "blind_spots": imp.blind_spots,
    }


# ---------------------------------------------------------------- diff impact


def _n(n: int, word: str, plural: str | None = None) -> str:
    return f"{n} {word if n == 1 else (plural or word + 's')}"


def _cap(items: list[str], limit: int) -> list[str]:
    return items[:limit] + ([f"  ... and {len(items) - limit} more."] if len(items) > limit else [])


def _lines_phrase(lines: list[int]) -> str:
    return f"{'line' if len(lines) == 1 else 'lines'} {ranges(lines)}"


def diff_text(d: DiffImpact, limit: int = 50) -> str:
    where = f"compared with {d.base}" if d.mode == "git" else "a patch that is not applied yet"
    counts = [_n(len(d.changed), "function") + " changed"]
    if d.removed:
        counts.append(_n(len(d.removed), "function") + " removed")
    if d.changed_vars:
        counts.append(_n(len(d.changed_vars), "declaration") + " changed")
    lines = [f"Diff impact, {where}: {_n(d.files, 'C/C++ file')}, {', '.join(counts)}."]
    if d.ignored:
        lines.append(
            f"({_n(d.ignored, 'other file')} in the diff {'is' if d.ignored == 1 else 'are'} not C or C++.)"
        )
    if d.changed:
        lines.append(f"Changed functions ({len(d.changed)}):")
        lines += _cap(
            [
                f"  - {c.fn.qualified_name}  {c.fn.where}  ("
                + ({"new": "new", "removed": "removed by the patch"}.get(c.state) or _n(c.lines, "line"))
                + ")"
                for c in d.changed
            ],
            limit,
        )
    if d.removed:
        lines.append(f"Removed functions ({len(d.removed)}):")
        rem = []
        for r in d.removed:
            still = (
                "still called by "
                + ", ".join(f"{c.qualified_name} ({c.where})" for c in r.still_called_by[:5])
                + (f" and {len(r.still_called_by) - 5} more" if len(r.still_called_by) > 5 else "")
                if r.still_called_by
                else "no remaining calls found"
            )
            rem.append(f"  - {r.name}  (was {r.file}:{r.line_start}-{r.line_end})  {still}")
        lines += _cap(rem, limit)
    if d.changed_vars:
        lines.append(f"Changed globals and fields ({len(d.changed_vars)}):")
        lines += _cap(
            [
                f"  - {v.name} ({v.kind})  {v.file}:{v.line}  used by {_n(v.users, 'function')}"
                for v in d.changed_vars
            ],
            limit,
        )
    hits = d.affected
    shown: list[Hit] = []
    if hits:
        by = Counter(h.confidence for h in hits)
        parts = ", ".join(f"{by[c]} {c}" for c in CONFIDENCE_ORDER if by[c])
        lines.append(f"May affect {_n(len(hits), 'other function')} ({parts}), nearest first:")
        level_lines, shown = _levels(hits, limit, via_always=True)
        lines += level_lines
    elif d.changed or d.changed_vars or d.removed:
        lines.append("No other function in the index calls or reads what changed.")
    lines += _legend(shown)

    cant: list[str] = []
    for o in d.outside:
        bits = []
        if o.lines:
            bits.append(f"{_lines_phrase(o.lines)} outside any function")
        if o.other_side:
            when = "removed" if d.mode == "git" else "added"
            side = "old" if d.mode == "git" else "new"
            bits.append(f"{when} {_lines_phrase(o.other_side)} ({side} numbering) outside any function")
        cant.append(f"  - {o.file}: {'; '.join(bits)}.")
    cant += [f"  - {f} was deleted." for f in d.deleted]
    cant += [
        f"  - {f}: could not read its old version; removed functions there are not listed."
        for f in d.unreadable
    ]
    cant += [
        f"  - {f} is new in the patch. It is analysed once it exists and is indexed." for f in d.new_files
    ]
    cant += [
        f"  - {f}: the patch does not match the file on disk, so it was skipped. Make the patch against the "
        "current file."
        for f in d.mismatched
    ]
    cant += [
        f"  - {f} is not in the index (not in compile_commands.json, excluded, or a header no indexed file "
        "includes)."
        for f in d.not_indexed
    ]
    if cant:
        lines.append("Changes AeroTrace could not follow:")
        lines += _cap(cant, limit)
    if d.blind_spots:
        lines.append("Limits of this answer:")
        lines += [f"  - {n}" for n in d.blind_spots]
    return "\n".join(lines)


def diff_json(d: DiffImpact, limit: int = 50) -> dict:
    by = Counter(h.confidence for h in d.affected)
    return {
        "mode": d.mode,
        "base": d.base,
        "depth": d.depth,
        "files": d.files,
        "ignored_files": d.ignored,
        "changed_functions": [
            {**c.fn.brief(), "changed_lines": c.lines, "state": c.state} for c in d.changed[:limit]
        ],
        "changed_functions_omitted": max(0, len(d.changed) - limit),
        "removed_functions": [
            {
                "name": r.name,
                "file": r.file,
                "old_lines": [r.line_start, r.line_end],
                "still_called_by": [c.brief() for c in r.still_called_by],
            }
            for r in d.removed[:limit]
        ],
        "changed_declarations": [
            {"name": v.name, "kind": v.kind, "file": v.file, "line": v.line, "users": v.users}
            for v in d.changed_vars[:limit]
        ],
        "total_affected": len(d.affected),
        "by_confidence": {c: by[c] for c in CONFIDENCE_ORDER if by[c]},
        "truncated_at_depth": d.truncated,
        "affected": [hit_json(h) for h in d.affected[:limit]],
        "omitted": max(0, len(d.affected) - limit),
        "outside_functions": [
            {"file": o.file, "lines": o.lines, "other_side_lines": o.other_side} for o in d.outside
        ],
        "deleted_files": d.deleted,
        "unreadable_old_versions": d.unreadable,
        "new_files": d.new_files,
        "mismatched_files": d.mismatched,
        "not_indexed": d.not_indexed,
        "blind_spots": d.blind_spots,
    }


# ---------------------------------------------------------------- signature impact


def _use_text(s: Site) -> str:
    detail = s.detail
    if s.broken:
        detail = detail.split("; ", 1)[1] if "; " in detail else ""
    if s.use == "discarded":
        return USE_WORDS["discarded"]
    if s.use == "used" and detail:
        return f"used {detail}"
    return USE_WORDS.get(s.use, s.use) + (f" {detail}" if detail else "")


def _site_line(s: Site, t: SignatureImpact, returns: bool) -> str:
    use = f"  result {_use_text(s)}" if returns else ""
    tag = "" if s.certainty == certainty.EXACT else f" [{s.certainty}]"
    args = ""
    if s.broken:
        args = f"  DOES NOT COMPILE NOW: passes {s.n_args} argument{'s' if s.n_args != 1 else ''}"
        if t.params is not None:
            args += f", it takes {t.params}{' or more' if t.variadic else ''}"
    return f"  - {s.caller.qualified_name}  {s.caller.file}:{s.line}{tag}{use}{args}"


def signature_text(r: SignatureImpact, limit: int = 50) -> str:
    t = r.target
    takes = (
        "an unknown number of parameters"
        if r.params is None
        else f"{_n(r.params, 'parameter')}{' and more (...)' if r.variadic else ''}"
    )
    callers = {s.caller.id for s in r.sites}
    lines = [
        f"Signature impact of {t.qualified_name} ({t.where}): {t.signature}, {takes}.",
        f"A change to its parameters or return type affects {_n(len(r.sites), 'call site')} in "
        f"{_n(len(callers), 'function')}, {_n(len(r.address_sites), 'place')} that {'uses' if len(r.address_sites) == 1 else 'use'} its address, and "
        f"{_n(len(r.pointer_sites), 'pointer call')} that may reach it.",
    ]
    returns = not r.returns_void
    if r.sites:
        lines.append(f"Call sites ({len(r.sites)}):")
        lines += _cap([_site_line(s, r, returns) for s in r.sites], limit)
    if returns and (r.sites or r.pointer_sites):
        used = r.uses()
        lines.append(
            "How callers use the return value: "
            + ", ".join(f"{used[u]} {USE_WORDS[u]}" for u in USES if used[u])
            + "."
        )
    elif not returns and r.sites:
        lines.append("It returns void, so no caller uses a return value.")
    if r.address_sites:
        lines.append(
            f"Places that use its address ({len(r.address_sites)}): stored, passed or compared as a pointer. "
            "Its type there must match the new signature:"
        )
        rows = []
        for a in r.address_sites:
            where = (
                f"in {a.in_function.qualified_name}"
                if a.in_function
                else f"in the initializer of {a.in_variable}"
                if a.in_variable
                else "at file scope"
            )
            rows.append(f"  - {a.file}:{a.line}  {where}: {a.how}")
        lines += _cap(rows, limit)
    if r.pointer_sites:
        lines.append(
            f"Calls through a pointer of its type that may reach it ({len(r.pointer_sites)}) [probable]:"
        )
        lines += _cap([_site_line(s, r, returns) for s in r.pointer_sites], limit)
    if r.upstream:
        lines.append(f"Callers further up ({r.depth} more step{'s' if r.depth != 1 else ''}), nearest first:")
        level_lines, shown = _levels(r.upstream, limit, via_always=True)
        lines += level_lines
        lines += _legend(shown)
    levels = {s.certainty for s in r.sites + r.pointer_sites}
    lines += [
        f"[{lv}] = {certainty.MEANING[lv]}"
        for lv in certainty.LEVELS
        if lv in levels and lv != certainty.EXACT
    ]
    if r.blind_spots:
        lines.append("Limits of this answer:")
        lines += [f"  - {n}" for n in r.blind_spots]
    return "\n".join(lines)


def _site_json(s: Site) -> dict:
    return {
        "caller": s.caller.brief(),
        "line": s.line,
        "column": s.col,
        "arguments": s.n_args,
        "use": s.use,
        "detail": s.detail,
        "compiles": not s.broken,
        "link": s.link,
        "certainty": s.certainty,
        "reason": s.reason,
    }


def signature_json(r: SignatureImpact, limit: int = 50) -> dict:
    used = r.uses()
    return {
        "target": r.target.brief(),
        "parameters": r.params,
        "variadic": r.variadic,
        "returns_void": r.returns_void,
        "call_sites": [_site_json(s) for s in r.sites[:limit]],
        "call_sites_total": len(r.sites),
        "return_value_uses": {u: used[u] for u in USES if used[u]},
        "address_sites": [
            {
                "file": a.file,
                "line": a.line,
                "in_function": a.in_function.brief() if a.in_function else None,
                "in_variable": a.in_variable,
                "how": a.how,
            }
            for a in r.address_sites[:limit]
        ],
        "pointer_call_sites": [_site_json(s) for s in r.pointer_sites[:limit]],
        "upstream": [hit_json(h) for h in r.upstream[:limit]],
        "upstream_total": len(r.upstream),
        "truncated_at_depth": r.truncated,
        "blind_spots": r.blind_spots,
    }
