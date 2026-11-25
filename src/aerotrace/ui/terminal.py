"""Terminal text for `aerotrace check`: headline first, grouped sections, one proof line at the end.

Meaning never depends on colour: every item has a status word, and colour (only on a TTY) is limited to
bold, red and yellow on the icon and the word. With NO_COLOR, TERM=dumb or a non-UTF-8 stream the icons
are plain ASCII. Lines are wrapped at word boundaries only when the caller passes a width (the CLI does
so on a terminal); in pipes, CI logs and MCP answers lines are not wrapped, so the text stays the same
everywhere and is easy to grep.
"""

from __future__ import annotations

import codecs
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping
from typing import TextIO

from aerotrace.ui.schema import CERTAINTIES, CERTAINTY_MEANING, check, node_word

ICONS = {
    # key: (unicode, ascii)
    "risk": ("⚠️ ", "[!]"),
    "clear": ("✅", "[ok]"),
    "info": ("\u2139\ufe0f ", "[i]"),  # information source emoji
    "unchecked": ("\u2753", "[?]"),  # question mark: the answer is UNKNOWN
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
    # NO_COLOR is about colour only; Unicode icons stay unless the stream can't show them.
    return (tty and not no_color and not dumb, utf8 and not dumb)


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


def _unknown_first(data: dict) -> str:
    """'UNKNOWN: AeroTrace could not check 2 parts of this change (a macro in defs.h). ...' (A-04)."""
    s = data.get("summary") or {}
    parts, reason = data["unchecked_parts"], data["unchecked_reason"]
    why = f" ({reason})" if reason else ""
    unk = s.get("unknown", 0)
    if parts and parts == unk:  # only things it reaches could not be followed
        return (
            f"UNKNOWN: AeroTrace could not follow {n_(unk, 'thing')} this change reaches{why}. "
            f"Check {'it' if unk == 1 else 'them'} by hand, or build."
        )
    return f"UNKNOWN: AeroTrace could not check {n_(parts, 'part')} of this change{why}. Build before you rely on it."


def headline(data: dict, all_hint: str = "") -> tuple[str, list[str]]:
    """(icon key, lines). The first line is the headline; it is chosen from the verdict and the counts.
    It says UNKNOWN whenever the verdict is `unknown`, and "Nothing found" only when it is `clear`."""
    st = data["status"]
    change = data.get("change") or {}
    if st == "no_index":
        return "risk", [
            "UNKNOWN: AeroTrace has no index for this repo yet, so it checked nothing.",
            data["message"],
        ]
    if st == "index_error":
        return "risk", [
            "UNKNOWN: AeroTrace could not read its index, so it checked nothing.",
            data["message"],
        ]
    if st == "nothing_to_check":
        where = _where(change) if change else ""
        gaps = not_followed_lines(data)
        if data["verdict"] == "unknown":
            lines = [_unknown_first(data), f"No change AeroTrace could check {where}."]
        else:
            # never a green line: nothing was checked
            lines = ["No C or C++ changes, so there was nothing to check."]
            if where:
                lines.append(_cap_first(where[1:-1]) + ".")  # "(compared with HEAD (abc))" -> "Compared ..."
        other = change.get("other_files", 0)
        if gaps:
            if other:
                lines.append(
                    f"({n_(other, 'other file')} in the diff {'is' if other == 1 else 'are'} not C or C++.)"
                )
            return "unchecked", lines
        if other:
            lines.append(
                f"{n_(other, 'other file')} in the diff {'is' if other == 1 else 'are'} not C or C++. "
                "AeroTrace does not follow build files, scripts or docs."
            )
            return "info", lines
        if data.get("message"):
            lines.append(data["message"])
        return "info", lines

    s = data["summary"]
    be, bp = s["breaks_exact"], s["breaks_probable"]
    direct, further = s["affected_direct"], s["affected_further"]
    aff, unk = direct + further, s["unknown"]
    verdict, parts = data["verdict"], data["unchecked_parts"]
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
    elif verdict == "unknown":
        icon, first = "unchecked", _unknown_first(data)
        if aff:
            more.append(f"it may affect {n_(aff, 'thing')} you didn't touch ({aff_txt})")
        if unk and parts != unk:
            more.append(f"{unk:,} {'is' if unk == 1 else 'are'} unknown")
    elif aff:
        icon = "risk"
        first = (
            f"This change may affect {n_(aff, 'thing')} you didn't touch ({aff_txt}). None is sure to break."
        )
    else:
        icon, first = "clear", "Nothing found that this change can break, in the code AeroTrace can see."
    if aff and (be or bp):
        more.append(f"{aff:,} may be affected ({aff_txt})")
    if be or bp:
        first = first[:-1] + f" ({_counts(be, bp, aff, unk)})."
        if data["unchecked"]:
            # the same first line says that the answer is not complete (A-04)
            first = first[:-1] + f"; {n_(parts, 'part')} could not be checked."
    lines = [first]
    if more:
        lines.append(_cap_first(_join(more)) + ".")
    lines.append(_you_changed(change))
    other = change.get("other_files", 0)
    if other:
        lines.append(f"({n_(other, 'other file')} in the diff {'is' if other == 1 else 'are'} not C or C++.)")
    if icon == "unchecked" and limits_above(data, icon):
        # nothing found here either, so what the build left out is said right away
        lines += [f"Index limit: {t}" for t in coverage_lines(data, all_hint)[1:]]
    if icon == "clear":
        lines.append("No other indexed function calls what changed or reads what it writes.")
        # A pass never hides what the index as a whole can't see; files outside the build come first.
        limits = coverage_lines(data, all_hint)[1:] + index_limits(data)
        lines += [f"Index limit: {t}" for t in limits[:INDEX_LIMITS_UNDER_PASS]]
        if len(limits) > INDEX_LIMITS_UNDER_PASS:
            lines.append(
                f"... and {len(limits) - INDEX_LIMITS_UNDER_PASS:,} more index limits, listed below."
            )
    return icon, lines


INDEX_LIMITS_UNDER_PASS = 5


def limits_above(data: dict, icon: str) -> bool:
    """True when nothing was found, so the files outside the build are named right below the headline."""
    if icon == "clear":
        return True
    s = data.get("summary") or {}
    found = s.get("affected_direct", 0) + s.get("affected_further", 0) + s.get("unknown", 0)
    return icon == "unchecked" and data["status"] == "ok" and not found


def _counts(be: int, bp: int, aff: int, unk: int) -> str:
    """'4 BREAKS, 19 AFFECTED': the status words of the lists below, with their counts."""
    parts = [(be, "BREAKS"), (bp, "MAY BREAK"), (aff, "AFFECTED"), (unk, "UNKNOWN")]
    return ", ".join(f"{n:,} {w}" for n, w in parts if n)


def coverage_lines(data: dict, all_hint: str = "") -> list[str]:
    """'Checked 117 of 117 files in this build (1 had parse errors).' and, when some repo sources are not
    in compile_commands.json, one short line saying so and naming up to 3 of them. Empty when the JSON has
    no coverage numbers."""
    p = data.get("proof") or {}
    if "build_files" not in p:
        return []
    errs = p["build_files_with_errors"]
    err_txt = f" ({errs:,} had parse errors)" if errs else ""
    out = [f"Checked {p['build_files_checked']:,} of {n_(p['build_files'], 'file')} in this build{err_txt}."]
    outside = p["outside_build"]
    if outside:
        names = p.get("outside_build_examples") or []
        more = outside > len(names)
        rest = outside - len(names)
        listed = (": " + ", ".join(names) + (f" and {rest:,} more" if rest else "")) if names else ""
        hint = f" ({all_hint})" if all_hint and more and "outside_build_files" not in p else ""
        out.append(
            f"{n_(outside, 'C/C++ file')} outside this build {'was' if outside == 1 else 'were'} not "
            f"checked{listed}{hint}."
        )
    return out


def index_limits(data: dict) -> list[str]:
    return [b["text"] for b in data["sections"]["blind_spots"] if b["scope"] == "index"]


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
    out += [
        f"{f} was deleted or renamed; code that still includes or names it is not followed."
        for f in nf["deleted"]
    ]
    out += [
        f"{f}: a build file changed. AeroTrace does not read build files." for f in nf.get("build_files", [])
    ]
    out += [
        f"{f} changed but the patch has no text for it, so it was not read." for f in nf.get("no_text", [])
    ]
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


def _gaps(o: _Out, data: dict, limit: int) -> None:
    gaps = not_followed_lines(data)
    if gaps:
        o.section(f"Changes AeroTrace could not follow ({len(gaps):,})")
        for g in gaps[:limit]:
            o.note("unknown", g)
        if len(gaps) > limit:
            o.lines.append(f"  ... and {len(gaps) - limit:,} more.")


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


# Control characters, bidi controls (which can reorder what is shown) and line/paragraph separators.
_UNSAFE = re.compile("[\x00-\x1f\x7f-\x9f\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069\u2028\u2029]")


def safe_text(s: str, ascii_only: bool = False) -> str:
    """`s` with unsafe characters shown as \\uXXXX, so a name can't send escape codes or reorder text."""
    s = _UNSAFE.sub(lambda m: f"\\u{ord(m.group()):04x}", s)
    return s.encode("ascii", "backslashreplace").decode("ascii") if ascii_only else s


def clean(v: object, ascii_only: bool = False) -> object:
    """Every string in `v` passed through safe_text."""
    if isinstance(v, str):
        return safe_text(v, ascii_only)
    if isinstance(v, list):
        return [clean(x, ascii_only) for x in v]
    if isinstance(v, dict):
        return {k: clean(x, ascii_only) for k, x in v.items()}
    return v


def _width(s: str) -> int:
    """Columns `s` takes on a terminal (ANSI codes take none, wide characters two)."""
    s = strip_ansi(s)
    return sum(
        # U+FE0F asks for the emoji form, which terminals draw two columns wide
        1
        if c == "\ufe0f"
        else 0
        if unicodedata.combining(c)
        else 2
        if unicodedata.east_asian_width(c) in "WF"
        else 1
        for c in s
    )


def _hang(line: str) -> int:
    """Where continuation lines start: under the text, after the indent and any icon."""
    plain = strip_ansi(line)
    indent = len(plain) - len(plain.lstrip(" "))
    text = plain[indent:]
    icons = sorted({f.rstrip() for pair in ICONS.values() for f in pair}, key=len, reverse=True)
    for icon in icons:  # longest first; the ASCII "[ ]" has a space inside
        if text.startswith(icon + " "):
            rest = text[len(icon) :]
            return _width(plain[: indent + len(icon)]) + len(rest) - len(rest.lstrip(" "))
    return indent


def wrap(text: str, width: int) -> str:
    """Every line longer than `width` broken at spaces, continuation lines indented under the text.
    A single word longer than the line (a long path) is never split."""
    out: list[str] = []
    for line in text.split("\n"):
        if _width(line) <= width:
            out.append(line)
            continue
        hang = " " * _hang(line)
        indent = len(line) - len(line.lstrip(" "))
        cur, words = line[:indent], line[indent:].split(" ")
        start = True
        for w in words:
            if start:
                cur, start = cur + w, False
            elif w == "" or _width(cur) + 1 + _width(w) <= width or cur.strip() == "":
                cur += " " + w
            else:
                out.append(cur.rstrip())
                cur = hang + w
        out.append(cur.rstrip())
    return "\n".join(out)


def render(
    data: dict,
    *,
    color: bool = False,
    unicode: bool = True,
    limit: int = 8,
    width: int | None = None,
    all_hint: str = "",
) -> str:
    """The whole terminal answer as one string (no trailing newline). `width` wraps long lines at word
    boundaries (None = no wrapping). `all_hint` names the option that lists the files outside the build
    (the CLI passes '--all lists them'; MCP passes nothing)."""
    text = _render(data, color=color, unicode=unicode, limit=limit, all_hint=all_hint)
    return wrap(text, width) if width else text


def _render(data: dict, *, color: bool, unicode: bool, limit: int, all_hint: str) -> str:
    check(data)
    data = clean(data, not unicode)  # type: ignore[assignment]
    o = _Out(color, unicode)
    icon, head = headline(data, all_hint)
    o.lines.append(o.paint(f"{o.icon(icon)} {head[0]}", "head"))
    o.lines += [f"    {line}" for line in head[1:]]
    if data["status"] != "ok":
        if data["status"] == "nothing_to_check":
            _gaps(o, data, limit)
        return "\n".join(o.lines)

    nodes = {n["id"]: n for n in data["nodes"]}
    sec = data["sections"]
    used: set[str] = set()
    changed = [n for n in data["nodes"] if n["status"] in ("changed", "new", "removed")]
    if changed:
        o.section(f"What changed ({len(changed):,})")
        for n in changed[:limit]:
            word = node_word(n["status"], n["certainty"]).ljust(WORD_WIDTH)
            why = f"  ({n['why'].rstrip('.')})" if n["why"] else ""
            o.lines.append(f"  {o.icon(n['status'])} {word}  {title(n)}  {n['file']}:{n['line']}{why}")
        if len(changed) > limit:
            o.lines.append(f"  ... and {len(changed) - limit:,} more.")
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

    _gaps(o, data, limit)

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
        # the same count as under the headline: files outside the build come first there
        shown_above = len(coverage_lines(data)[1:]) + len(spots)
        if scope == "index" and icon == "clear" and shown_above <= INDEX_LIMITS_UNDER_PASS:
            continue  # already printed under the headline
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
    cov = coverage_lines(data, all_hint)
    if cov:
        o.section(cov[0])
        if not limits_above(data, icon):  # under "nothing found" it is already right below the headline
            o.lines += cov[1:]
        outside = data["proof"].get("outside_build_files")
        if outside:
            o.section(f"Files outside this build ({len(outside):,})")
            o.lines += [f"  {f}" for f in outside]
    o.section(proof_line(data))
    return "\n".join(o.lines)
