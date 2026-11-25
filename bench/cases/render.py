"""Write bench/cases/CASES.md from bench/cases/cases.json (so the readable list can't drift)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> None:
    d = json.loads((HERE / "cases.json").read_text())
    out = [(HERE / "CASES.head.md").read_text().rstrip(), ""]
    out += [
        "| id | kind | level | symbol | where | counts (regex) | why this case |",
        "|---|---|---|---|---|---|---|",
    ]
    for c in d["cases"]:
        counts = ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in c.get("counts", {}).items() if v)
        where = f"`{c['file']}:{c['line']}`" if "line" in c else f"`{c.get('file', '')}`"
        why = d["kinds"][c["kind"]]["why"]
        extra = f" Type `{c['type']}`." if c["kind"] == "field_type" else ""
        out.append(
            f"| {c['id']} | {c['kind']} | {c['difficulty']} | `{c['symbol']}` | {where} | {counts} | "
            f"{why[:1].upper() + why[1:]}.{extra} |"
        )
    out += ["", "## Kinds of change", "", "| kind | level | the edit |", "|---|---|---|"]
    out += [f"| {k} | {v['difficulty']} | {v['edit']} |" for k, v in d["kinds"].items()]
    out += ["", "## Pool sizes and skips", ""]
    for repo, pools in d["pools"].items():
        n = sum(1 for s in d["skipped"] if s["repo"] == repo)
        out.append(
            f"- {repo}: {pools['defs']} function definitions, {pools['fields']} fields, "
            f"{pools['globals']} compiled file-scope variables, {pools['macros']} function-like macros. "
            f"{n} candidates skipped (all in `cases.json`, with reasons)."
        )
    sys.stdout.write("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
