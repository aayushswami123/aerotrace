"""Pure comparison logic for the gate: counts in, verdict out. No compiler, no AeroTrace, no files.

A "data" dict is what run_gate.py saves as baseline.json:
    {"measured": "<commit>", "cases": {id: {"build": ..., "expect": ..., "sites": ..., "error": "",
                                           "patch": {"rc": 1, "class": "correct", "before": "0 false safe"},
                                           "git": {...}}}}
A mode key is missing when the case does not run in that mode.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MODES = ("patch", "git")
CLASSES = ("correct", "honest unknown", "false safe", "weak", "false alarm")
KEYS = ("correct", "honest_unknown", "false_safe", "weak", "false_alarm", "false_unknown", "other", "cases")
# These must not go up. "correct" must not go down.
MUST_NOT_RISE = ("false_safe", "weak", "false_alarm", "false_unknown", "other")


def _key(cls: str) -> str:
    return cls.replace(" ", "_")


def counts_from_classes(classes: list[str]) -> dict[str, int]:
    """Count classes. A false unknown also counts as a false alarm."""
    t = dict.fromkeys(KEYS, 0)
    for cls in classes:
        t["cases"] += 1
        if cls == "false unknown":
            t["false_unknown"] += 1
            t["false_alarm"] += 1
        else:
            t[_key(cls) if cls in CLASSES else "other"] += 1
    return t


def class_of(cases: dict[str, Any], case_id: str, mode: str) -> str | None:
    entry = cases.get(case_id, {}).get(mode)
    return entry["class"] if entry else None


def counts(cases: dict[str, Any], mode: str) -> dict[str, int]:
    return counts_from_classes([e[mode]["class"] for e in cases.values() if mode in e])


def before_counts(cases: dict[str, Any], mode: str) -> dict[str, int] | None:
    cells = [e[mode].get("before") for e in cases.values() if mode in e]
    if not cells or any(c is None for c in cells):
        return None
    return counts_from_classes([c.split(" ", 1)[1] for c in cells])


@dataclass
class Verdict:
    failures: list[str] = field(default_factory=list)
    better: list[str] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)  # "id (mode): from -> to", sorted

    @property
    def ok(self) -> bool:
        return not self.failures


def compare(
    base: dict[str, Any], cur: dict[str, Any], known: dict[tuple[str, str], str] | None = None
) -> Verdict:
    """Compare a run with the saved baseline. Fails only on a regression, an error, or a case set change."""
    known = known or {}
    v = Verdict()
    bc, cc = base["cases"], cur["cases"]
    for i in sorted(set(bc) - set(cc)):
        v.failures.append(f"case missing from this run (it is in the baseline): {i}")
    for i in sorted(set(cc) - set(bc)):
        v.failures.append(f"case not in the baseline: {i}")
    for i in sorted(cc):
        if cc[i].get("error"):
            v.failures.append(f"{i}: ERROR: {cc[i]['error']}")
    for mode in MODES:
        b, c = counts(bc, mode), counts(cc, mode)
        for k in MUST_NOT_RISE:
            if c[k] > b[k]:
                v.failures.append(f"{mode} mode: {k.replace('_', ' ')} went up, {b[k]} -> {c[k]}")
            elif c[k] < b[k]:
                v.better.append(f"{mode} mode: {k.replace('_', ' ')} went down, {b[k]} -> {c[k]}")
        if c["correct"] < b["correct"]:
            v.failures.append(f"{mode} mode: correct went down, {b['correct']} -> {c['correct']}")
        elif c["correct"] > b["correct"]:
            v.better.append(f"{mode} mode: correct went up, {b['correct']} -> {c['correct']}")
        for i in sorted(set(bc) & set(cc)):
            was, now = class_of(bc, i, mode), class_of(cc, i, mode)
            if was != now:
                v.changes.append(f"{i} ({mode}): {was or '-'} -> {now or '-'}")
    for i in sorted(cc):
        for mode in MODES:
            if class_of(cc, i, mode) == "false safe" and (i, mode) not in known:
                v.failures.append(f"{i} ({mode} mode): false safe, and not in the known limits")
    return v


def known_block(
    cur: dict[str, Any], known: dict[tuple[str, str], str], issue: str
) -> tuple[list[str], list[str]]:
    """Lines for the known-limits block, and notes for known limits that stopped being false safe."""
    lines = [f"Known limits (false safe, tracked in {issue})"]
    better: list[str] = []
    for (i, mode), why in sorted(known.items()):
        entry = cur["cases"].get(i, {}).get(mode)
        if entry is None:
            lines.append(f"  {i} ({mode} mode): not run. {why}")
            continue
        lines.append(f"  {i} ({mode} mode): exit {entry['rc']}, {entry['class']}. {why}")
        if entry["class"] != "false safe":
            better.append(
                f"known limit {i} ({mode} mode) is no longer false safe: remove it from known_limits.toml"
            )
    return lines, better


def summary_lines(
    cur: dict[str, Any], base: dict[str, Any] | None = None, with_before: bool = False
) -> list[str]:
    out: list[str] = []
    for mode in MODES:
        c = counts(cur["cases"], mode)
        b = counts(base["cases"], mode) if base else None

        def n(k: str, c: dict[str, int] = c, b: dict[str, int] | None = b) -> str:
            return f"{c[k]}" + (f" (baseline {b[k]})" if b else "")

        out += [
            f"{mode} mode ({c['cases']} cases)",
            f"  correct:        {n('correct')}",
            f"  honest unknown: {n('honest_unknown')}",
            f"  false safe:     {n('false_safe')}",
            f"  weak:           {n('weak')}",
            f"  false alarm:    {n('false_alarm')} (of which false unknown: {n('false_unknown')})",
        ]
        if c["other"]:
            out.append(f"  other exit code: {n('other')}")
        bc = before_counts(cur["cases"], mode) if with_before else None
        if bc:
            out += [
                "  before (older build):",
                f"    correct {bc['correct']}, honest unknown {bc['honest_unknown']}, false safe {bc['false_safe']}, "
                f"weak {bc['weak']}, false alarm {bc['false_alarm']} (false unknown {bc['false_unknown']})",
            ]
        out.append("")
    return out


def table_rows(cases: dict[str, Any], with_before: bool) -> tuple[list[str], list[list[str]]]:
    def cell(e: dict[str, Any], mode: str) -> str:
        if e.get("error"):
            return "ERROR"
        m = e.get(mode)
        return f"{m['rc']} {m['class']}" if m else "-"

    def before(e: dict[str, Any], mode: str) -> str:
        m = e.get(mode)
        return (m or {}).get("before") or ("-" if not e.get("error") else "ERROR")

    head = ["id", "build", "patch", "git", "failing site"]
    if with_before:
        head = ["id", "build", "patch before", "patch", "git before", "git", "failing site"]
    rows = []
    for i in sorted(cases):
        e = cases[i]
        build = e["build"]
        if build in ("fails", "passes") and e["expect"] in ("fails", "passes") and build != e["expect"]:
            build += f" (expected {e['expect']})"
        if with_before:
            rows.append(
                [i, build, before(e, "patch"), cell(e, "patch"), before(e, "git"), cell(e, "git"), e["sites"]]
            )
        else:
            rows.append([i, build, cell(e, "patch"), cell(e, "git"), e["sites"]])
    return head, rows


def render_text(cases: dict[str, Any], with_before: bool) -> list[str]:
    head, rows = table_rows(cases, with_before)
    widths = [max(len(x[i]) for x in [head, *rows]) for i in range(len(head))]
    return ["  ".join(c.ljust(widths[i]) for i, c in enumerate(row)).rstrip() for row in [head, *rows]]


def render_markdown(data: dict[str, Any]) -> str:
    cases = data["cases"]
    with_before = any(m.get("before") for e in cases.values() for k, m in e.items() if k in MODES)
    head, rows = table_rows(cases, with_before)
    out = [
        "# Gate baseline",
        "",
        f"AeroTrace code measured: `{data['measured']}`. Cases: {len(cases)}. "
        "This file is generated from `baseline.json`. How to read it: see `README.md`.",
        "",
        "| " + " | ".join(head) + " |",
        "|" + "---|" * len(head),
        *["| " + " | ".join(r) + " |" for r in rows],
        "",
        "```text",
        *summary_lines(data, None, with_before),
    ]
    fs = sorted({i for i, e in cases.items() for m in MODES if m in e and e[m]["class"] == "false safe"})
    out += ["false safe ids: " + (", ".join(fs) if fs else "none"), "```", ""]
    return "\n".join(out)
