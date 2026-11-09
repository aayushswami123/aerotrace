"""Which source lines are compiled in this build: run `cc -E` with each file's flags from
compile_commands.json and keep the (file, line) pairs that survive preprocessing.

A line inside a disabled `#if` does not appear in the output, so it is "not compiled".
Does not use AeroTrace.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

MARK = re.compile(r'^# (\d+) "([^"]+)"')


def _one(entry: dict) -> dict[str, set[int]]:
    args = [a for a in entry["arguments"] if a not in ("-c",)]
    if "-o" in args:
        i = args.index("-o")
        del args[i : i + 2]
    cmd = [args[0], "-E", *args[1:]]
    r = subprocess.run(cmd, cwd=entry["directory"], capture_output=True, text=True, errors="replace")
    live: dict[str, set[int]] = {}
    cur, line = None, 0
    for s in r.stdout.split("\n"):
        m = MARK.match(s)
        if m:
            line = int(m.group(1))
            cur = os.path.realpath(os.path.join(entry["directory"], m.group(2)))
            continue
        if cur and s.strip():
            live.setdefault(cur, set()).add(line)
        line += 1
    return live


def live_lines(root: Path) -> dict[str, set[int]]:
    """realpath -> set of compiled line numbers, merged over every file in compile_commands.json."""
    entries = json.loads((root / "compile_commands.json").read_text())
    merged: dict[str, set[int]] = {}
    with ThreadPoolExecutor(8) as ex:
        for live in ex.map(_one, entries):
            for k, v in live.items():
                merged.setdefault(k, set()).update(v)
    return merged
