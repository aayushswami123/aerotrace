"""Pure comparison logic for the gate: counts in, verdict out. No compiler, no AeroTrace, no files.

A "data" dict is what run_gate.py saves as baseline.json:
    {"measured": "<commit>", "cases": {id: {"build": ..., "expect": ..., "sites": ..., "error": "",
                                           "patch": {"rc": 1, "class": "correct", "before": "0 false safe"},
                                           "git": {...}}}}
A mode key is missing when the case does not run in that mode.
"""

from __future__ import annotations

import math
import re
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
    info: list[str] = field(default_factory=list)  # reason count changes: never a failure

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
    v.info = reason_changes(bc, cc)
    for i in sorted(cc):
        for mode in MODES:
            if class_of(cc, i, mode) == "false safe" and (i, mode) not in known:
                v.failures.append(f"{i} ({mode} mode): false safe, and not in the known limits")
    return v


# -- why an answer is UNKNOWN (info only: a reason never fails the gate) --

# (key in change.not_followed, label), in a fixed order. "outside" uses the item's own "what".
NOT_FOLLOWED = (
    ("outside", "code outside any function"),
    ("mismatched", "the patch does not match"),
    ("unreadable", "the old version of a file could not be read"),
    ("not_indexed", "a changed file is not in the index"),
    ("new_files", "a new file is not in the build yet"),
    ("deleted", "a file was deleted or renamed"),
    ("build_files", "a build file changed"),
    ("no_text", "a changed file has no text in the patch"),
)
# Ordered: the first match wins. Names of files and symbols never go into a label.
UNCHECKED = (
    (r"named in .*\(a file outside the build\)", "named in a file outside the build"),
    (r"named in .*\(code AeroTrace did not compile\)", "named in code that was not compiled"),
    (r"^\d+ calls? of .* not compiled", "a call not compiled with the new signature"),
    (r"^The prototype of .* is removed", "a prototype removed but the function is still defined"),
    (r"relied on a removed prototype", "files relied on a removed prototype"),
    (r"^overrides of ", "overrides of a changed method"),
    (r"^tables or pointers that name ", "a table or pointer names the function"),
    (r"^the head of ", "the head of a function changed"),
    (r"^the type of ", "the type of a variable changed"),
    (r"the layout of", "struct layout changed"),
)
OTHER = "other"


def _outside_label(what: str) -> str:
    """The item's own "what" ("a macro", "an include"), without any name in it."""
    if not what:
        return LABEL_OUTSIDE
    if what.startswith("the declaration of"):
        return "a declaration outside a function"
    return what if "outside" in what else f"{what} outside a function"


LABEL_OUTSIDE = "code outside any function"


def _first_not_followed(doc: dict[str, Any]) -> str:
    nf = (doc.get("change") or {}).get("not_followed") or {}
    for key, label in NOT_FOLLOWED:
        items = nf.get(key)
        if items:
            if key == "outside" and isinstance(items[0], dict):
                return _outside_label(str(items[0].get("what") or ""))
            return label
    return ""


def unknown_reason(doc: dict[str, Any]) -> str:
    """A short, stable label for why a `check --json` answer is UNKNOWN. No file or symbol names."""
    kind = _first_not_followed(doc)
    if doc.get("status") != "ok":
        status = f"no check: {doc.get('status')}"
        return f"{status} ({kind})" if kind else status
    if kind:
        return kind
    raw = doc.get("unchecked_reason") or ""
    for pattern, label in UNCHECKED:
        if re.search(pattern, raw):
            return label
    # check.to_contract puts the unknown nodes last, as "<name> in <file>"
    if any(
        n.get("status") == "unknown" and raw == f"{n.get('name')} in {n.get('file')}"
        for n in doc.get("nodes") or []
    ):
        return "an UNKNOWN item"
    return OTHER


def reason_table(cases: dict[str, Any]) -> list[tuple[str, int, int, int]]:
    """(reason, honest unknown, false unknown, total) for every exit-2 answer, sorted by reason."""
    t: dict[str, list[int]] = {}
    for e in cases.values():
        for mode in MODES:
            m = e.get(mode)
            if m and m["rc"] == 2:
                row = t.setdefault(m.get("reason") or "(not recorded)", [0, 0])
                row[1 if m["class"] == "false unknown" else 0] += 1
    return [(r, h, f, h + f) for r, (h, f) in sorted(t.items())]


def reason_lines(cases: dict[str, Any]) -> list[str]:
    rows = reason_table(cases)
    total = sum(r[3] for r in rows)
    n = sum(1 for e in cases.values() for m in MODES if m in e)
    out = ["UNKNOWN by reason", ""]
    if rows:
        head = ["reason", "honest unknown", "false unknown", "total"]
        body = [[r, str(h), str(f), str(t)] for r, h, f, t in rows]
        w = [max(len(x[i]) for x in [head, *body]) for i in range(4)]
        for row in [head, *body]:
            out.append("  ".join(c.ljust(w[i]) if i == 0 else c.rjust(w[i]) for i, c in enumerate(row)))
        out.append("")
    pct = f"{100 * total / n:.1f}" if n else "0.0"
    out.append(f"UNKNOWN: {total} of {n} answers ({pct}%); quality bar: under 2%")
    return out


def reason_changes(base: dict[str, Any], cur: dict[str, Any]) -> list[str]:
    """Info lines for reasons whose count changed. Empty when the baseline has no reasons (old file)."""
    if not any(m.get("reason") for e in base.values() for mode in MODES if (m := e.get(mode))):
        return []
    was = {r: t for r, _, _, t in reason_table(base)}
    now = {r: t for r, _, _, t in reason_table(cur)}
    return [
        f"UNKNOWN reason '{r}': {was.get(r, 0)} -> {now.get(r, 0)}"
        for r in sorted(set(was) | set(now))
        if was.get(r, 0) != now.get(r, 0)
    ]


def percentile(values: list[float], p: float) -> float:
    """Nearest-rank percentile (p in 1..100). 0.0 for no values."""
    if not values:
        return 0.0
    xs = sorted(values)
    return xs[max(1, math.ceil(p / 100 * len(xs))) - 1]


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
        *reason_lines(cases),
    ]
    fs = sorted({i for i, e in cases.items() for m in MODES if m in e and e[m]["class"] == "false safe"})
    out += ["false safe ids: " + (", ".join(fs) if fs else "none"), "```", ""]
    return "\n".join(out)


def expectation_error(
    case_id: str, expect: str, built_ok: bool, listed_sites: list[str], actual_sites: list[str]
) -> str:
    """Error text if case.toml disagrees with the real build of the changed tree, else an empty string."""
    if expect in ("fails", "passes"):
        actual = "passes" if built_ok else "fails"
        if actual != expect:
            return f"{case_id}: case.toml says expect_build = {expect!r} but the changed tree {actual}"
    if expect == "fails":
        missing = [x for x in listed_sites if x not in actual_sites]
        if missing:
            return (
                f"{case_id}: failing_sites {missing} are not among the real failures {sorted(actual_sites)}"
            )
    return ""
