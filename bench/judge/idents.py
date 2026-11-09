"""idents.py ROOT NAME [FILE_GLOB...]: print every use of the identifier NAME outside comments and strings.

Exit 0 when there is none, 1 otherwise. Default globs: **/*.c **/*.h
"""

import pathlib
import re
import sys

root, name = pathlib.Path(sys.argv[1]), sys.argv[2]
globs = sys.argv[3:] or ["**/*.c", "**/*.h"]
LEX = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'', re.S)
found = 0
for g in globs:
    for p in sorted(root.glob(g)):
        text = LEX.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), p.read_text(errors="replace"))
        for m in re.finditer(r"\b%s\b" % re.escape(name), text):
            found += 1
            print(f"{p.relative_to(root)}:{text[: m.start()].count(chr(10)) + 1}")
sys.exit(1 if found else 0)
