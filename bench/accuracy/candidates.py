"""List grep hits for every sampled item, for the hand check. Does not use AeroTrace.

Usage: python3 -I bench/accuracy/candidates.py <redis-root> <sqlite-root> sample.json > candidates.json

Each hit has: file:line, the line text, whether the line is compiled in this build (`cc -E` with the
index's flags, see live.py), and the function the line is in (regex + brace counting, see cfuncs.py).
These are candidates only. truth.json is written by hand from them, after reading every hit.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from cfuncs import enclosing, functions
from live import live_lines
from sample import searched_files


def main() -> None:
    roots = {"redis": Path(sys.argv[1]), "sqlite": Path(sys.argv[2])}
    sample = json.loads(Path(sys.argv[3]).read_text())
    out = []
    for repo, root in roots.items():
        live = live_lines(root)
        files = searched_files(root, repo)
        funcs = {p: functions(p) for p in files}
        texts = {p: p.read_text(errors="replace").split("\n") for p in files}
        items = [("function", x) for x in sample["functions"] if x["repo"] == repo]
        items += [("field", x) for x in sample["fields"] if x["repo"] == repo]
        for kind, x in items:
            if kind == "function":
                pat = re.compile(r"\b" + re.escape(x["id"].rsplit(":", 1)[1]) + r"\b")
            else:
                pat = re.compile(r"(?:\.|->)\s*" + re.escape(x["id"].split(".", 1)[1]) + r"\b")
            hits = []
            for p in files:
                lv = live.get(os.path.realpath(p), set())
                for n, line in enumerate(texts[p], 1):
                    if pat.search(line):
                        hits.append(
                            {
                                "at": f"{p.relative_to(root).as_posix()}:{n}",
                                "compiled": n in lv,
                                "in": enclosing(funcs[p], n),
                                "text": line.strip()[:160],
                            }
                        )
            out.append({"repo": repo, "kind": kind, "id": x["id"], "hits": hits})
    json.dump(out, sys.stdout, indent=1)
    print()


if __name__ == "__main__":
    main()
