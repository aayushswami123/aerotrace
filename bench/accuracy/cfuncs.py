"""Simple C source scanning used to build the benchmark sample and truth candidates.

Does not use AeroTrace. Plain regex and brace counting: good enough for Redis and SQLite style
(a function definition starts at column 0 and its body ends with `}` at column 0). Anything this
misses is caught, or not, by the hand check; it is a tool for finding candidates, not the truth.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

KEYWORDS = {"if", "while", "for", "switch", "return", "sizeof", "do", "else", "case", "typedef", "struct"}
# A line starting at column 0 that names a function: optional return type, then name( ...
DEF_START = re.compile(r"^(?!#)(?:[A-Za-z_][\w \t\*]*?[ \t\*])?([A-Za-z_]\w*)[ \t]*\(")


def blank_comments_and_strings(text: str) -> str:
    """Replace comments, string and char literals by spaces, keeping newlines (line numbers stay right)."""
    out = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(re.sub(r"[^\n]", " ", text[i:j]))
            i = j
        elif text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif c in "\"'":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            j = min(j + 1, n)
            out.append(c + re.sub(r"[^\n]", " ", text[i + 1 : j - 1]) + c if j - i >= 2 else text[i:j])
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def functions(path: Path) -> list[dict]:
    """Function definitions in one file: name, start line, end line (1-based)."""
    text = blank_comments_and_strings(path.read_text(errors="replace"))
    lines = text.split("\n")
    found = []
    i = 0
    while i < len(lines):
        m = DEF_START.match(lines[i])
        if not m or m.group(1) in KEYWORDS or lines[i][:1] in " \t":
            i += 1
            continue
        # Find the closing ')' of the parameter list, then require '{' next (not ';' or ',').
        depth, j, col = 0, i, m.end(1)
        k = None
        while j < len(lines) and j < i + 40:
            s = lines[j]
            start = col if j == i else 0
            for p in range(start, len(s)):
                if s[p] == "(":
                    depth += 1
                elif s[p] == ")":
                    depth -= 1
                    if depth == 0:
                        k = (j, p)
                        break
            if k:
                break
            j += 1
        if not k:
            i += 1
            continue
        rest = lines[k[0]][k[1] + 1 :]
        jj = k[0]
        while not rest.strip() and jj + 1 < len(lines):
            jj += 1
            rest = lines[jj]
        if not rest.strip().startswith("{"):
            i += 1
            continue
        # Body: count braces from the '{'.
        depth, end = 0, None
        for q in range(jj, len(lines)):
            for ch in lines[q]:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        end = q
                        break
            if end is not None:
                break
        if end is None:
            i += 1
            continue
        found.append({"name": m.group(1), "start": i + 1, "end": end + 1})
        i = end + 1
    return found


def compiled_files(root: Path) -> list[Path]:
    """The .c files listed in the repo's compile_commands.json (written by tests/realrepo/repos.py)."""
    entries = json.loads((root / "compile_commands.json").read_text())
    return sorted(Path(e["directory"]) / e["file"] for e in entries)


def enclosing(funcs: list[dict], line: int) -> str | None:
    for f in funcs:
        if f["start"] <= line <= f["end"]:
            return f["name"]
    return None
