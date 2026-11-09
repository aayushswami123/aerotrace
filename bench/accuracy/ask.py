"""Ask AeroTrace about every sampled item through its normal CLI and store the raw JSON.

Usage: python3 -I bench/accuracy/ask.py <redis-root> <sqlite-root> sample.json <out-dir>
Run from the AeroTrace checkout (it calls `uv run aerotrace`). Both repos must be indexed first.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def main() -> None:
    roots = {"redis": sys.argv[1], "sqlite": sys.argv[2]}
    sample = json.loads(Path(sys.argv[3]).read_text())
    out = Path(sys.argv[4])
    out.mkdir(parents=True, exist_ok=True)
    jobs = []
    for x in sample["functions"]:
        file, name = x["id"].rsplit(":", 1)
        jobs.append((x, ["callers", f"{name}@{file}", "--depth", "1", "--limit", "1000", "--json"]))
    for x in sample["fields"]:
        struct, field = x["id"].split(".", 1)
        jobs.append((x, ["data", f"{struct}::{field}", "--json", "--all"]))
    for x, args in jobs:
        cmd = ["uv", "run", "aerotrace", *args, "--root", roots[x["repo"]]]
        r = subprocess.run(cmd, capture_output=True, text=True)
        try:
            answer = json.loads(r.stdout)
        except json.JSONDecodeError:
            answer = None
        rec = {"cmd": " ".join(["aerotrace", *args]), "exit": r.returncode, "answer": answer}
        if answer is None:
            rec["stdout"], rec["stderr"] = r.stdout[-2000:], r.stderr[-2000:]
        safe = x["id"].replace("/", "_").replace(":", "__")
        (out / f"{x['repo']}-{safe}.json").write_text(json.dumps(rec, indent=1) + "\n")
        print(x["id"], r.returncode)


if __name__ == "__main__":
    main()
