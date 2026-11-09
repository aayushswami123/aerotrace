"""Terminal text for `aerotrace check`: headline first, grouped sections, one proof line at the end.

Meaning never depends on colour: every item has a status word, and colour (only on a TTY) is limited to
bold, red and yellow on the icon and the word. With NO_COLOR, TERM=dumb or a non-UTF-8 stream the icons
are plain ASCII. Lines are not wrapped, so the text stays the same everywhere and is easy to grep.
"""

from __future__ import annotations

import codecs
import re
from collections import Counter
from collections.abc import Mapping
from typing import TextIO

from aerotrace.ui.schema import CERTAINTIES, CERTAINTY_MEANING, check, node_word

ICONS = {
    # key: (unicode, ascii)
    "risk": ("⚠️ ", "[!]"),
    "clear": ("✅", "[ok]"),
    "info": ("\u2139\ufe0f ", "[i]"),  # information source emoji
    "breaks": ("✖", "[x]"),
    "affected": ("▲", "[~]"),
    "unknown": ("?", "[?]"),
    "no_effect": ("·", "[-]"),
    "changed": ("◆", "[*]"),
    "new": ("◆", "[*]"),
    "removed": ("◆", "[*]"),
    "no_test": ("○", "[ ]"),
}
_SGR = {"breaks": "1;31", "affected": "1;33", "unknown": "1", "head": "1"}
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
WORD_WIDTH = 9  # "MAY BREAK"


def style_for(stream: TextIO, env: Mapping[str, str]) -> tuple[bool, bool]:
    """(color, unicode) for this output stream and environment."""
    no_color = env.get("NO_COLOR", "") != ""  # https://no-color.org: set and not empty
    dumb = env.get("TERM", "") == "dumb"
    try:
        utf8 = codecs.lookup(getattr(stream, "encoding", None) or "ascii").name == "utf-8"
    except LookupError:
        utf8 = False
    try:
        tty = bool(stream.isatty())
    except (AttributeError, ValueError):
        tty = False
    return (tty and not no_color and not dumb, utf8 and not no_color and not dumb)


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def n_(n: int, word: str, plural: str | None = None) -> str:
    return f"{n:,} {word if n == 1 else (plural or word + 's')}"


def ranges(lines: list[int]) -> str:
    """[1,2,3,7] -> '1-3, 7'."""
    out: list[list[int]] = []
    for n in sorted(set(lines)):
        if out and out[-1][1] == n - 1:
            out[-1][1] = n
        else:
            out.append([n, n])
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in out)


def _join(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def _cap_first(s: str) -> str:
    return s[:1].upper() + s[1:]


# ---------------------------------------------------------------- shared wording (terminal and HTML)


def headline(data: dict) -> tuple[str, list[str]]:
    """(icon key, lines). The first line is the headline; it is chosen from counts only."""
    st = data["status"]
    change = data.get("change") or {}
    if st == "no_index":
        return "risk", ["AeroTrace has no index for this repo yet, so it checked nothing.", data["message"]]
    if st == "index_error":
        return "risk", ["AeroTrace could not read its index, so it checked nothing.", data["message"]]
    if st == "nothing_to_check":
        where = _where(change) if change else ""
        lines = [f"No C or C++ changes to check{(' ' + where) if where else ''}."]
        other = change.get("other_files", 0)
        if other:
            lines.append(
                f"{n_(other, 'other file')} in the diff {'is' if other == 1 else 'are'} not C or C++. "
                "AeroTrace does not follow build files, scripts or docs."
            )
            return "info", lines
        if data.get("message"):
            lines.append(data["message"])
        return "clear", lines

    s = data["summary"]
    be, bp = s["breaks_exact"], s["breaks_probable"]
    direct, further = s["affected_direct"], s["affected_further"]
    aff, unk, nf = direct + further, s["unknown"], s["not_followed"]
    aff_txt = ", ".join(
        p for p in (f"{direct:,} direct" if direct else "", f"{further:,} further up" if further else "") if p
    )
    more: list[str] = []
    if be:
        icon, first = "risk", f"This change can break {n_(be, 'thing')} you didn't touch."
        if bp:
            more.append(f"{bp:,} more may not build or link")
    elif bp:
        icon, first = "risk", f"{n_(bp, 'thing')} you didn't touch may not build or link."
    elif aff:
        icon = "risk"
        first = (
            f"This change may affect {n_(aff, 'thing')} you didn't touch ({aff_txt}). None is sure to break."
        )
    elif unk:
        icon = "risk"
        first = (
            f"{n_(unk, 'thing')} this change reaches {'is' if unk == 1 else 'are'} unknown: AeroTrace could not "
            f"follow {'it' if unk == 1 else 'them'}. Check by hand."
        )
    elif nf:
        icon = "info"
        first = f"Nothing found that this change can break, but {n_(nf, 'part')} of the change could not be followed."
    else:
        icon, first = "clear", "Nothing found that this change can break, in the code AeroTrace can see."
    if aff and (be or bp):
        more.append(f"{aff:,} may be affected ({aff_txt})")
    if unk and (be or bp or aff):
        more.append(f"{unk:,} {'is' if unk == 1 else 'are'} unknown")
    if nf and (be or bp or aff or unk):
        more.append(f"{n_(nf, 'part')} of the change could not be followed")
    lines = [first]
    if more:
        lines.append(_cap_first(_join(more)) + ".")
    lines.append(_you_changed(change))
    other = change.get("other_files", 0)
    if other:
        lines.append(f"({n_(other, 'other file')} in the diff {'is' if other == 1 else 'are'} not C or C++.)")
    if icon == "clear":
        lines.append("No other indexed function calls what changed or reads what it writes.")
    return icon, lines


def _where(change: dict) -> str:
    if change.get("mode") == "git":
        return f"(compared with {change['base']})" if change.get("base") else "(compared with git)"
    return "(a patch that is not applied yet)"


def _you_changed(change: dict) -> str:
    parts = []
    if change["changed_functions"]:
        parts.append(f"changed {n_(change['changed_functions'], 'function')}")
    if change["new_functions"]:
        parts.append(f"added {n_(change['new_functions'], 'new function')}")
    if change["removed_functions"]:
        parts.append(f"removed {n_(change['removed_functions'], 'function')}")
    if change["changed_declarations"]:
        parts.append(f"changed {n_(change['changed_declarations'], 'declaration')}")
    what = _join(parts) if parts else "changed no function or declaration AeroTrace can place"
    return f"You {what} in {n_(change['files'], 'C/C++ file')} {_where(change)}."


def proof_line(data: dict) -> str:
    p = data["proof"]
    return (
        f"index: {n_(p['index_files'], 'file')}, {n_(p['index_functions'], 'function')}; "
        f"followed callers up to {n_(p['depth'], 'step')} (stopped early: {'yes' if p['stopped_early'] else 'no'})"
    )


def not_followed_lines(data: dict) -> list[str]:
    """One plain sentence per part of the change AeroTrace could not follow."""
    change = data["change"]
    nf = change["not_followed"]
    git = change["mode"] == "git"
    out: list[str] = []
    for o in nf["outside"]:
        bits = []
        if o["lines"]:
            bits.append(f"{_lines(o['lines'])} outside any function")
        if o["other_side_lines"]:
            when, side = ("removed", "old") if git else ("added", "new")
            bits.append(f"{when} {_lines(o['other_side_lines'])} ({side} numbering) outside any function")
        out.append(f"{o['file']}: {'; '.join(bits) or 'changes outside any function'}.")
    out += [f"{f} was deleted." for f in nf["deleted"]]
    out += [
        f"{f}: could not read its old version; removed functions there are not listed."
        for f in nf["unreadable"]
    ]
    out += [
        f"{f} is new in the patch. It is analysed once it exists and is indexed." for f in nf["new_files"]
    ]
    out += [
        f"{f}: the patch does not match the file on disk, so it was skipped. Make the patch against the current file."
        for f in nf["mismatched"]
    ]
    out += [
        f"{f} is not in the index (not in compile_commands.json, excluded, or a header no indexed file includes)."
        for f in nf["not_indexed"]
    ]
    return out


def _lines(lines: list[int]) -> str:
    return f"{'line' if len(lines) == 1 else 'lines'} {ranges(lines)}"


def title(n: dict) -> str:
    return n["name"] if n["kind"] == "function" else f"{n['name']} ({n['kind'].replace('_', ' ')})"


def detail(n: dict) -> str:
    why = n["why"].rstrip().rstrip(".")
    return f"{why}. Link: {n['certainty']}." if why else f"Link: {n['certainty']}."


# ---------------------------------------------------------------- the renderer


class _Out:
    def __init__(self, color: bool, unicode: bool):
        self.color, self.unicode = color, unicode
        self.lines: list[str] = []

    def icon(self, key: str) -> str:
        return ICONS[key][0 if self.unicode else 1]

    def paint(self, text: str, key: str) -> str:
        code = _SGR.get(key)
        return f"\x1b[{code}m{text}\x1b[0m" if self.color and code else text

    def section(self, heading: str) -> None:
        if self.lines and self.lines[-1] != "":
            self.lines.append("")
        self.lines.append(heading)

    def item(self, key: str, word: str, head: str, body: str) -> None:
        icon = self.icon(key)
        lead = f"  {icon} "
        self.lines.append(lead + self.paint(word.ljust(WORD_WIDTH), key) + f"  {head}")
        self.lines.append(" " * (len(lead) + WORD_WIDTH + 2) + body)

    def note(self, key: str, text: str) -> None:
        self.lines.append(f"  {self.paint(self.icon(key), key)} {text}")


_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _clean(v: object) -> object:
    """Data strings with control characters replaced, so a name can't send escape codes to the terminal."""
    if isinstance(v, str):
        return _CONTROL.sub("?", v)
    if isinstance(v, list):
        return [_clean(x) for x in v]
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    return v


def render(data: dict, *, color: bool = False, unicode: bool = True, limit: int = 8) -> str:
    """The whole terminal answer as one string (no trailing newline)."""
    check(data)
    data = _clean(data)  # type: ignore[assignment]
    o = _Out(color, unicode)
    icon, head = headline(data)
    o.lines.append(o.paint(f"{o.icon(icon)} {head[0]}", "head"))
    o.lines += [f"    {line}" for line in head[1:]]
    if data["status"] != "ok":
        return "\n".join(o.lines)

    nodes = {n["id"]: n for n in data["nodes"]}
    sec = data["sections"]
    used: set[str] = set()
    for key, heading in (("functions", "Functions"), ("data", "Data")):
        ids = sec[key]
        if not ids:
            continue
        o.section(f"{heading} ({len(ids):,})")
        for nid in ids[:limit]:
            n = nodes[nid]
            used.add(n["certainty"])
            o.item(
                n["status"],
                node_word(n["status"], n["certainty"]),
                f"{title(n)}  {n['file']}:{n['line']}",
                detail(n),
            )
        rest = ids[limit:]
        if rest:
            by = Counter(node_word(nodes[i]["status"], nodes[i]["certainty"]) for i in rest)
            kinds = ", ".join(f"{c:,} {w}" for w, c in sorted(by.items(), key=lambda kv: (-kv[1], kv[0])))
            o.lines.append(f"  ... and {len(rest):,} more ({kinds}). Raise --limit or use --html to see all.")

    gaps = not_followed_lines(data)
    if gaps:
        o.section(f"Changes AeroTrace could not follow ({len(gaps):,})")
        for g in gaps[:limit]:
            o.note("unknown", g)
        if len(gaps) > limit:
            o.lines.append(f"  ... and {len(gaps) - limit:,} more.")

    tests = sec.get("tests_to_run")
    if tests is None:
        o.section("Tests: not known yet (no test map in the index).")
    elif not tests:
        o.section("Tests to run: no test found that reaches this change.")
    else:
        o.section(f"Tests to run ({len(tests):,})")
        width = min(40, max(len(t["name"]) for t in tests[:limit]))
        for t in tests[:limit]:
            names = [nodes[c]["name"] for c in t["covers"]]
            covers = ", ".join(names[:5]) + (f" and {len(names) - 5:,} more" if len(names) > 5 else "")
            o.lines.append(f"  {t['name'].ljust(width)}  covers {covers}" if names else f"  {t['name']}")
        if len(tests) > limit:
            o.lines.append(f"  ... and {len(tests) - limit:,} more.")

    untested = sec.get("untested_changes")
    if untested:
        o.section(f"Untested changes ({len(untested):,})")
        for u in untested[:limit]:
            n = nodes[u["node"]]
            o.item("no_test", "NO TEST", f"{title(n)}  {n['file']}:{n['line']}", u["why"])
        if len(untested) > limit:
            o.lines.append(f"  ... and {len(untested) - limit:,} more.")

    for scope, heading in (("change", "Blind spots in this change"), ("index", "Limits of the index")):
        spots = [b for b in sec["blind_spots"] if b["scope"] == scope]
        if spots:
            o.section(f"{heading} ({len(spots):,})")
            for b in spots[:limit]:
                o.note("unknown", b["text"])
            if len(spots) > limit:
                o.lines.append(f"  ... and {len(spots) - limit:,} more.")

    left_out = data["omitted"]["nodes"]
    if left_out:
        o.section(f"({n_(left_out, 'more node')} left out of this report to keep it small.)")
    legend = [f"{c} = {CERTAINTY_MEANING[c]}" for c in CERTAINTIES if c in used and c != "exact"]
    if legend:
        o.section("Link: " + "; ".join(legend) + ".")
    o.section(proof_line(data))
    return "\n".join(o.lines)
