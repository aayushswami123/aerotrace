"""Print the Markdown tables for docs/benchmark/RESULTS.md from <run-dir>/results.json.

Usage: python3 -I bench/truth/tables.py <run-dir>
Every number in RESULTS.md's tables comes from here; none is typed by hand.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

TOOLS = [("aerotrace", "AeroTrace (claimed breaks)"), ("aerotrace_anywhere", "AeroTrace (whole answer)"),
         ("clangd", "clangd refs"), ("cscope", "cscope -L0"), ("ripgrep", "ripgrep -w")]  # fmt: skip


def pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.0f}%"


def summary(groups: dict, key: str, title: str) -> list[str]:
    g = groups.get(key)
    if not g:
        return []
    out = [f"**{title}** ({g['aerotrace']['cases']} cases, {g['aerotrace']['truth_sites']} break sites)", "",
           "| tool | found | recall | false alarms | median time | tokens |", "|---|---|---|---|---|---|"]  # fmt: skip
    for t, name in TOOLS:
        x = g[t]
        secs = "n/a" if x["median_seconds"] is None else f"{x['median_seconds']:.3f} s"
        tok = "n/a" if x["tokens_total"] is None else f"{x['tokens_total']:,}"
        if t == "aerotrace_anywhere":
            secs = tok = "same"
        out.append(
            f"| {name} | {x['found']}/{x['truth_sites']} | {pct(x['recall'])} | {x['false_alarms']} | {secs} | {tok} |"
        )
    return [*out, ""]


def per_case(rows: list[dict]) -> list[str]:
    out = ["| case | kind | level | breaks | AeroTrace | AeroTrace whole | clangd | cscope | ripgrep |",
           "|---|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for r in rows:
        cells = []
        for t in ("aerotrace", "aerotrace_anywhere", "clangd", "cscope", "ripgrep"):
            x = r["tools"]["aerotrace"].get("anywhere") if t == "aerotrace_anywhere" else r["tools"][t]
            if x is None or "error" in x:
                cells.append("error")
                continue
            cells.append(f"{x['found']}/{x['truth_sites']}, {x['false_alarms']} fa")
        out.append(
            f"| {r['id']} | {r['kind']} | {r['level']} | {len(r['truth'])} | " + " | ".join(cells) + " |"
        )
    return out


def main() -> None:
    d = json.loads((Path(sys.argv[1]) / "results.json").read_text())
    g = d["groups"]
    lines = []
    lines += summary(g, "all/easy", "Easy cases, all repos")
    lines += summary(g, "all/hard_without_curl-09", "Hard cases without curl-09 (extra view)")
    lines += summary(g, "all/hard", "Hard cases, all repos")
    lines += summary(g, "all/all", "Easy and hard together (build breaks only)")
    lines += summary(g, "all/might_break", "Might-break cases (hand-checked truth)")
    for repo in ("redis", "sqlite", "curl"):
        for level in ("easy", "hard"):
            lines += summary(g, f"{repo}/{level}", f"{repo}, {level}")
    lines += ["### Every case", "", "`found/breaks, N fa` (fa = false alarms)", "", *per_case(d["cases"])]
    print("\n".join(lines))


if __name__ == "__main__":
    main()
