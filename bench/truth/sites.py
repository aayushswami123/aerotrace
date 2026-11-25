"""Turn a file:line into a site, the same way for the truth and for every tool's answer.

A site is the function that contains the line (`file:function`), the file-scope table whose initializer
contains it (`file:@name`), or the line itself (`file:@lineN`) for declarations, `#define`s and other
file-scope lines. Rows of Redis's commands.def are all `src/commands.def:@redisCommandTable`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "accuracy"))
from cfuncs import blank_comments_and_strings, functions

# `name[] = {` at the start of a line, after a type, or after the `}` that closes an inline struct type
# (`} const maybe_feature[] = {`).
INIT_START = re.compile(r"^(?:\}\s*)?(?:[A-Za-z_][\w \t\*]*?\b)?([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*=\s*\{")


class Sites:
    def __init__(self, root: Path):
        self.root = root
        self._cache: dict[str, tuple[list[dict], list[tuple[int, int, str]]]] = {}

    def _scan(self, rel: str) -> tuple[list[dict], list[tuple[int, int, str]]]:
        if rel not in self._cache:
            p = self.root / rel
            fns = functions(p)
            lines = blank_comments_and_strings(p.read_text(errors="replace")).split("\n")
            in_fn = set()
            for f in fns:
                in_fn.update(range(f["start"], f["end"] + 1))
            inits, i = [], 0
            while i < len(lines):
                m = INIT_START.match(lines[i])
                if m and (i + 1) not in in_fn and "(" not in lines[i].split("=")[0]:
                    depth, j = 0, i
                    while j < len(lines):
                        part = (
                            lines[j][lines[j].index("=") :] if j == i else lines[j]
                        )  # braces after `=` only
                        if not part.lstrip().startswith("#"):
                            depth += part.count("{") - part.count("}")
                        if depth <= 0 and j >= i and "{" in "".join(lines[i : j + 1]):
                            break
                        j += 1
                    inits.append((i + 1, j + 1, m.group(1)))
                    i = j + 1
                    continue
                i += 1
            self._cache[rel] = (fns, inits)
        return self._cache[rel]

    def site(self, rel: str, line: int) -> str:
        if rel == "src/commands.def":
            return "src/commands.def:@redisCommandTable"
        fns, inits = self._scan(rel)
        for f in fns:
            if f["start"] <= line <= f["end"]:
                return f"{rel}:{f['name']}"
        for s, e, name in inits:
            if s <= line <= e:
                return f"{rel}:@{name}"
        return f"{rel}:@line{line}"


def old_changed_lines(patch: str) -> dict[str, set[int]]:
    """Lines of the unchanged files that a unified diff removes or replaces (old side)."""
    out: dict[str, set[int]] = {}
    cur, old = None, 0
    for ln in patch.split("\n"):
        if ln.startswith("--- a/"):
            cur = ln[6:].strip()
            out.setdefault(cur, set())
        elif ln.startswith("@@"):
            m = re.match(r"@@ -(\d+)", ln)
            old = int(m.group(1)) if m else 0
        elif cur and ln.startswith("-") and not ln.startswith("---"):
            out[cur].add(old)
            old += 1
        elif cur and ln.startswith("+") and not ln.startswith("+++"):
            out[cur].add(max(old - 1, 1))  # an insertion sits next to the line before it
            out[cur].add(old)
        elif cur and ln.startswith(" "):
            old += 1
    return out
