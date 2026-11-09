"""Short, ranked, plain-English answers for agents and people. Lists are capped and say what was left out."""

from __future__ import annotations

from collections import Counter

from aerotrace.analysis.diff import DiffImpact, ranges
from aerotrace.analysis.graph import CONFIDENCE_MEANING, CONFIDENCE_ORDER, Hit, Impact, Walk
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
