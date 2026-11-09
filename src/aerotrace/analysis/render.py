"""Short, ranked, plain-English answers for agents and people. Lists are capped and say what was left out."""

from __future__ import annotations

from collections import Counter

from aerotrace.analysis import certainty
from aerotrace.analysis.diff import DiffImpact, ranges
from aerotrace.analysis.graph import CONFIDENCE_MEANING, CONFIDENCE_ORDER, Hit, Impact, Walk
from aerotrace.analysis.signature import IGNORING, USE_WORDS, USES, SignatureImpact, Site, UpstreamGroup
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


# Default caps for the text answer. Calls that do not compile, group headers' counts, the summary, legends
# and limits are never cut. `limit` sets every list cap at once; `show_all` removes them.
SITE_CAP = 15
ADDRESS_CAP = 10
POINTER_CAP = 10
DIRECT_GROUP_CAP = 10
POINTER_GROUP_CAP = 5
GROUP_HEADER_CAP = 10
CLI_MORE = "use --all to see every one, or --limit N"
MCP_MORE = "call again with show_all=true, or a bigger limit"


def _split(r: SignatureImpact, sites: list[Site]) -> str:
    """'20 use it (3 stored, ...), 8 ignore it (7 ignored, 1 discarded with (void))' for these sites."""
    used = r.uses(sites)
    use = [u for u in USES if u not in IGNORING and used[u]]
    ign = [u for u in USES if u in IGNORING and used[u]]
    n_use, n_ign = sum(used[u] for u in use), sum(used[u] for u in ign)
    parts = []
    if n_use:
        parts.append(
            f"{n_use} {'uses' if n_use == 1 else 'use'} it ({', '.join(f'{used[u]} {USE_WORDS[u]}' for u in use)})"
        )
    if n_ign:
        detail = ", ".join(f"{used[u]} {USE_WORDS[u]}" for u in ign)
        parts.append(
            ("all ignore it" if not n_use else f"{n_ign} {'ignores' if n_ign == 1 else 'ignore'} it")
            + f" ({detail})"
        )
    return ", ".join(parts)


def _capped_sites(r: SignatureImpact, sites: list[Site], cap: int | None) -> tuple[list[Site], int]:
    """Ranked sites to show: every call that does not compile, then up to `cap` of the others."""
    ranked = r.ranked(sites)
    broken = [s for s in ranked if s.broken]
    rest = [s for s in ranked if not s.broken]
    shown = rest if cap is None else rest[:cap]
    return broken + shown, len(rest) - len(shown)


def _more(n: int, what: str, hint: str) -> str:
    return f"  ... and {n} more {what} ({hint})."


def _upstream_line(h: Hit, also: int, target: str, indent: str) -> str:
    tag = "" if h.confidence == "exact" else f" [{h.confidence}]"
    via = f" via {h.via}" if h.via else ""
    more = f" (also reached through {_n(also, 'other caller')} of {target})" if also else ""
    return f"{indent}- {h.fn.qualified_name}  {h.fn.where}{tag}{via}{more}"


def _group_header(g: UpstreamGroup) -> str:
    n = _n(len(g.members), "caller")
    if g.kind == "direct":
        probable = sum(1 for h in g.members if h.confidence != "exact")
        extra = f"; {probable} of them probable" if probable else ""
        return f"  {n}, reached through its direct call sites{extra}:"
    e = g.entry
    lines = ", ".join(str(x) for x in g.lines)
    at = f" at {e.file}:{lines}" if lines else ""  # type: ignore[union-attr]
    return f"  {n}, reached through the pointer call in {e.qualified_name}(){at} [probable]:"  # type: ignore[union-attr]


def signature_text(
    r: SignatureImpact, limit: int | None = None, show_all: bool = False, more: str = CLI_MORE
) -> str:
    def cap(default: int) -> int | None:
        return None if show_all else (limit if limit is not None else default)

    t = r.target
    takes = (
        "an unknown number of parameters"
        if r.params is None
        else f"{_n(r.params, 'parameter')}{' and more (...)' if r.variadic else ''}"
    )
    callers = {s.caller.id for s in r.sites}
    files = r.files()
    level = Counter(s.certainty for s in r.sites)
    broken = sum(1 for s in r.sites + r.pointer_sites if s.broken)
    through_ptr = sum(len(g.members) for g in r.groups if g.kind == "pointer")
    lines = [
        f"Signature impact of {t.qualified_name} ({t.where}): {t.signature}, {takes}.",
        f"A change to its parameters or return type affects {_n(len(r.sites), 'call site')} in "
        f"{_n(len(callers), 'function')}, {_n(len(r.address_sites), 'place')} that "
        f"{'uses' if len(r.address_sites) == 1 else 'use'} its address, and "
        f"{_n(len(r.pointer_sites), 'pointer call')} that may reach it.",
        f"Summary: {_n(len(r.sites), 'call site')} in {_n(len(files), 'file')} ("
        + ", ".join(f"{level[lv]} {lv}" for lv in certainty.LEVELS)
        + f"); {broken} {'does' if broken == 1 else 'do'} not compile now; "
        f"{_n(len(r.pointer_sites), 'pointer call')} (probable); "
        f"{_n(len(r.upstream), 'caller')} further up, {through_ptr} of them only through pointers.",
    ]
    returns = not r.returns_void
    if returns and r.sites:
        lines.append(f"How callers use the return value: {_split(r, r.sites)}.")
    if returns and r.pointer_sites:
        lines.append(f"How pointer calls use it: {_split(r, r.pointer_sites)}.")
    if not returns and (r.sites or r.pointer_sites):
        lines.append("It returns void, so no caller uses a return value.")
    if files:
        lines.append("Call sites per file, most first: " + ", ".join(f"{f} {n}" for f, n in files) + ".")
    if r.sites:
        shown, left = _capped_sites(r, r.sites, cap(SITE_CAP))
        lines.append(f"Call sites ({len(r.sites)}), most important first:")
        lines += [_site_line(s, r, returns) for s in shown]
        if left:
            lines.append(_more(left, "call sites", more))
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
        c = cap(ADDRESS_CAP)
        lines += rows if c is None else rows[:c]
        if c is not None and len(rows) > c:
            lines.append(_more(len(rows) - c, "places", more))
    if r.pointer_sites:
        lines.append(
            f"Calls through a pointer of its type that may reach it ({len(r.pointer_sites)}) [probable]:"
        )
        shown, left = _capped_sites(r, r.pointer_sites, cap(POINTER_CAP))
        lines += [_site_line(s, r, returns) for s in shown]
        if left:
            lines.append(_more(left, "pointer calls", more))
    if r.upstream:
        lines.append(
            f"Callers further up ({len(r.upstream)}, {r.depth} more step{'s' if r.depth != 1 else ''}), "
            f"grouped by the caller of {t.name} they come through. Each is listed once:"
        )
        hc = None if show_all else max(GROUP_HEADER_CAP, limit or 0)
        for g in r.groups if hc is None else r.groups[:hc]:
            lines.append(_group_header(g))
            c = cap(DIRECT_GROUP_CAP if g.kind == "direct" else POINTER_GROUP_CAP)
            members = g.members if c is None else g.members[:c]
            lines += [_upstream_line(h, g.also.get(h.fn.id, 0), t.name, "    ") for h in members]
            if len(g.members) > len(members):
                lines.append("  " + _more(len(g.members) - len(members), "in this group", more))
        if hc is not None and len(r.groups) > hc:
            rest = r.groups[hc:]
            lines.append(
                f"  ... and {_n(len(rest), 'more group')} ({_n(sum(len(g.members) for g in rest), 'caller')}) "
                f"({more})."
            )
        lines += _legend(r.upstream)
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


def _site_json(r: SignatureImpact, s: Site) -> dict:
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
        "rank_group": r.rank_group(s),
    }


def signature_json(r: SignatureImpact, limit: int | None = 50, show_all: bool = False) -> dict:
    """Lists keep their old order and cap (50); new keys only add to it. `show_all` removes the caps."""
    n = None if show_all else (50 if limit is None else limit)

    def head(items: list) -> list:
        return items if n is None else items[:n]

    def left(items: list) -> int:
        return 0 if n is None else max(0, len(items) - n)

    used = r.uses()
    level = Counter(s.certainty for s in r.sites)
    return {
        "target": r.target.brief(),
        "parameters": r.params,
        "variadic": r.variadic,
        "returns_void": r.returns_void,
        "call_sites": [_site_json(r, s) for s in head(r.sites)],
        "call_sites_total": len(r.sites),
        "call_sites_omitted": left(r.sites),
        "not_compiling_call_sites": [_site_json(r, s) for s in r.sites + r.pointer_sites if s.broken],
        "return_value_uses": {u: used[u] for u in USES if used[u]},
        "address_sites": [
            {
                "file": a.file,
                "line": a.line,
                "in_function": a.in_function.brief() if a.in_function else None,
                "in_variable": a.in_variable,
                "how": a.how,
            }
            for a in head(r.address_sites)
        ],
        "address_sites_total": len(r.address_sites),
        "address_sites_omitted": left(r.address_sites),
        "pointer_call_sites": [_site_json(r, s) for s in head(r.pointer_sites)],
        "pointer_call_sites_total": len(r.pointer_sites),
        "pointer_call_sites_omitted": left(r.pointer_sites),
        "upstream": [hit_json(h) for h in head(r.upstream)],
        "upstream_total": len(r.upstream),
        "upstream_omitted": left(r.upstream),
        "upstream_groups": [
            {
                "kind": g.kind,
                "entry": g.entry.brief() if g.entry else None,
                "pointer_call_lines": g.lines,
                "total": len(g.members),
                "members": [
                    {
                        **h.fn.brief(),
                        "confidence": h.confidence,
                        "also_reached_through": g.also.get(h.fn.id, 0),
                    }
                    for h in head(g.members)
                ],
                "members_omitted": left(g.members),
            }
            for g in r.groups
        ],
        "summary": {
            "call_sites": len(r.sites),
            "files": len(r.files()),
            "by_certainty": {lv: level[lv] for lv in certainty.LEVELS},
            "not_compiling": sum(1 for s in r.sites + r.pointer_sites if s.broken),
            "pointer_call_sites": len(r.pointer_sites),
            "address_sites": len(r.address_sites),
            "upstream": len(r.upstream),
            "upstream_only_through_pointers": sum(len(g.members) for g in r.groups if g.kind == "pointer"),
            "direct_uses": {
                u: c for u, c in sorted(r.uses(r.sites).items(), key=lambda kv: USES.index(kv[0]))
            },
            "pointer_uses": {
                u: c for u, c in sorted(r.uses(r.pointer_sites).items(), key=lambda kv: USES.index(kv[0]))
            },
            "call_sites_per_file": [{"file": f, "count": c} for f, c in r.files()],
        },
        "truncated_at_depth": r.truncated,
        "blind_spots": r.blind_spots,
    }
