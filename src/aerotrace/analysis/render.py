"""Short, ranked, plain-English answers for agents and people. Lists are capped and say what was left out."""

from __future__ import annotations

from collections import Counter

from aerotrace.analysis.graph import CONFIDENCE_MEANING, CONFIDENCE_ORDER, Hit, Impact, Walk
from aerotrace.analysis.symbols import Fn


def _hit_line(h: Hit, show_via: bool) -> str:
    via = f" via {h.via}" if show_via and h.via else ""
    tag = "" if h.confidence == "exact" else f" [{h.confidence}]"
    return f"  - {h.fn.qualified_name}  {h.fn.where}{tag}{via}"


def _levels(hits: list[Hit], limit: int) -> tuple[list[str], list[Hit]]:
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
            out.append(_hit_line(h, show_via=d > 1))
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
