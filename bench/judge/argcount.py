"""argcount.py ROOT NAME N [FILE_GLOB...]: list every use NAME(...) whose argument count is not N.

Comments and string literals are blanked first (line numbers kept), so text like `name()` in a comment
is ignored. Definitions and prototypes are checked too: their parameter count must also be N.
Exit 0 when every use has N arguments, 1 otherwise. Default globs: **/*.c **/*.h
"""

import pathlib
import re
import sys

root, name, want = pathlib.Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
globs = sys.argv[4:] or ["**/*.c", "**/*.h"]
LEX = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'', re.S)


def blank(m: re.Match) -> str:
    return re.sub(r"[^\n]", " ", m.group(0))


bad = 0
for g in globs:
    for p in sorted(root.glob(g)):
        text = LEX.sub(blank, p.read_text(errors="replace"))
        for m in re.finditer(r"\b%s\s*\(" % re.escape(name), text):
            i, depth, args = m.end(), 1, 0
            while i < len(text) and depth:
                ch = text[i]
                if ch in "([{":
                    depth += 1
                elif ch in ")]}":
                    depth -= 1
                if depth == 1 and ch == ",":
                    args += 1
                i += 1
            inner = text[m.end() : i - 1].strip()
            n = 0 if inner in ("", "void") else args + 1
            if n != want:
                bad += 1
                line = text[: m.start()].count("\n") + 1
                print(f"{p.relative_to(root)}:{line}: {name} has {n} arguments, want {want}")
sys.exit(1 if bad else 0)
