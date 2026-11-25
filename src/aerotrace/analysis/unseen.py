"""Places that name a symbol but that the index has no use of (review of #84, the audit's A-40 direction).

When a change alters something other code uses by name (a function or global that is removed, made
static, or whose signature or type changed), code AeroTrace did not compile can still use it: a branch of
an `#if` that is off with these flags, a file outside compile_commands.json, a macro body. This is a plain
word search over the repo's C and C++ files, comments and strings left out. A place counts as unseen when
the index records no use of that name on that line. It is cheap: each file is read once per check, and
only when it contains one of the names. Constructors and destructors count only in files outside the build (a class names them in its own
declaration), and there any use of the class name counts (`X x(1)`, `new X`). Table (`.def`, `.tbl`) and assembly (`.s`, `.S`, `.asm`) files are searched too. Struct fields are not searched (names like `len` are everywhere).
"""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from aerotrace.index import store

MAX_BYTES = 128 * 1024 * 1024  # read at most this much; past it, the search is not complete
_SKIP_DIRS = {"node_modules", "__pycache__"}
# Table files (`.def`, `.tbl`) are included by C files; in assembly the C symbol may carry a leading `_`.
TABLE_ASM_EXTS = {".def", ".tbl", ".s", ".asm"}
ASM_EXTS = {".s", ".asm"}
_JUNK = re.compile(r"//[^\n]*|/\*.*?\*/|\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'", re.S)


@dataclass(frozen=True)
class Place:
    file: str
    line: int
    name: str
    outside: bool  # the file is not in the index (not in the build)


@dataclass
class Found:
    places: list[Place]  # sorted by (file, line, name)
    capped: bool  # MAX_BYTES was reached: the search is not complete


def _blank(m: re.Match) -> str:
    return re.sub(r"[^\n]", " ", m.group(0))


def code_files(root: Path) -> list[str]:
    """Repo-relative C and C++ files, sorted. Hidden folders (.git, .aerotrace, ...) are skipped."""
    from aerotrace.analysis.diff import CODE_EXTS

    exts = CODE_EXTS | TABLE_ASM_EXTS
    out = []
    top = str(root)
    for d, dirs, files in os.walk(top):
        dirs[:] = sorted(x for x in dirs if not x.startswith(".") and x not in _SKIP_DIRS)
        rel = os.path.relpath(d, top).replace(os.sep, "/")
        for f in files:
            if os.path.splitext(f)[1].lower() in exts:
                out.append(f if rel == "." else f"{rel}/{f}")
    return sorted(out)


def _seen(conn: sqlite3.Connection, names: list[str]) -> tuple[set[tuple[str, int, str]], dict[str, list]]:
    """(file, line, name) where the index has a use, definition or declaration of that name, and the line
    ranges of indexed functions per file."""
    seen: set[tuple[str, int, str]] = set()
    q = ",".join("?" * len(names))
    for r in conn.execute(
        f"""SELECT f.file, c.line, t.name FROM call_sites c JOIN functions t ON t.id = c.callee
            JOIN functions f ON f.id = c.caller WHERE t.name IN ({q})""",
        names,
    ):
        seen.add((r[0], r[1], r[2]))
    for r in conn.execute(
        f"""SELECT a.file, a.line, t.name FROM address_sites a JOIN functions t ON t.id = a.function_id
            WHERE t.name IN ({q})""",
        names,
    ):
        seen.add((r[0], r[1], r[2]))
    for r in conn.execute(
        f"SELECT file, line_start, line_end, name FROM functions WHERE name IN ({q})", names
    ):
        for n in range(r[1], min(r[2], r[1] + 4) + 1):  # the head may span a few lines
            seen.add((r[0], n, r[3]))
    for r in conn.execute(f"SELECT file, line, name FROM variables WHERE name IN ({q})", names):
        seen.add((r[0], r[1], r[2]))
    for r in conn.execute(
        f"""SELECT f.file, e.line, v.name FROM edges e JOIN variables v ON v.id = e.dst
            JOIN functions f ON f.id = e.src WHERE v.name IN ({q})""",
        names,
    ):
        seen.add((r[0], r[1], r[2]))
    ranges: dict[str, list] = {}
    for r in conn.execute("SELECT file, line_start, line_end FROM functions ORDER BY file, line_start"):
        ranges.setdefault(r[0], []).append((r[1], r[2]))
    return seen, ranges


def _pattern(name: str, kind: str) -> re.Pattern:
    n = re.escape(name)
    if kind == "asm":  # `bl _helper`, `call helper`: `_` is a word character, so allow it in front
        return re.compile(rf"(?<![\w.])_?{n}\b")
    if kind == "global":
        return re.compile(rf"(?<![\w.])(?<!->){n}\b")
    member = kind == "method"
    guard = r"(?<!\w)" if member else r"(?<![\w.])(?<!->)"
    # a call, the address, or an entry of a table: f(  &f  f,  f}
    if kind == "special":  # a constructor or destructor: the class name in any form (`X x(1)`, `new X`)
        return re.compile(rf"(?<![\w.])(?<!->){re.escape(name.lstrip('~'))}\b")
    return re.compile(rf"{guard}{n}\s*\(|&\s*{n}\b|{guard}{n}\s*[,}}]")


def _declares(text: str, name: str, kind: str) -> bool:
    """A file-scope line that only declares the name (a prototype or an extern)."""
    if kind == "global":
        return "extern" in text
    return re.search(rf"[\w*)\]&]\s+\**\s*(\w+::)*{re.escape(name)}\s*\(", text) is not None or bool(
        re.match(rf"\s*\**\s*(\w+::)*{re.escape(name)}\s*\(", text)  # `int\nf(...)` style heads
    )


def find(
    conn: sqlite3.Connection,
    root: Path,
    names: dict[str, str],
    skip: dict[str, set[int]] | None = None,
    max_bytes: int = MAX_BYTES,
) -> Found:
    """Places in the repo that name one of `names` ({name: 'function' | 'method' | 'global'}) where the
    index has no use of it. `skip` holds lines to leave out (file -> lines the patch itself removes)."""
    if not names:
        return Found([], False)
    skip = skip or {}
    plain = sorted(names)
    word = {n: n.lstrip("~") for n in plain}  # `~X` is searched as the class name X
    seen, ranges = _seen(conn, plain)
    indexed = store.indexed_paths(conn)
    quick = re.compile(
        r"(?<![A-Za-z0-9])_?(?:" + "|".join(re.escape(w) for w in sorted(set(word.values()))) + r")\b"
    )
    quick_bytes = re.compile(quick.pattern.encode("utf-8"))
    raw = [w.encode("utf-8") for w in word.values()]
    pats = {n: _pattern(n, names[n]) for n in plain}
    asm_pats = {n: _pattern(n, "asm") for n in plain}
    places: list[Place] = []
    read = 0
    for rel in code_files(root):
        try:
            with open(os.path.join(root, rel), "rb") as fh:
                data = fh.read()
        except OSError:
            continue
        read += len(data)
        if read > max_bytes:
            return Found(sorted(places, key=lambda p: (p.file, p.line, p.name)), True)
        if not any(b in data for b in raw) or not quick_bytes.search(data):
            continue
        text = data.decode("utf-8", "replace")
        code = _JUNK.sub(_blank, text)
        lines = code.split("\n")
        outside = rel not in indexed
        is_asm = os.path.splitext(rel)[1].lower() in ASM_EXTS
        fn_ranges = ranges.get(rel, [])
        for no, line in enumerate(lines, 1):
            if not any(w in line for w in word.values()) or no in skip.get(rel, ()):
                continue
            for n in plain:
                if word[n] not in line or (rel, no, n) in seen:
                    continue
                if names[n] == "special" and not outside:
                    continue  # the class's own declarations name its constructor and destructor
                if not (asm_pats if is_asm else pats)[n].search(line):
                    continue
                at_file_scope = not outside and not any(s <= no <= e for s, e in fn_ranges)
                if at_file_scope and not line.lstrip().startswith("#") and _declares(line, n, names[n]):
                    continue  # a prototype or extern at file scope
                places.append(Place(rel, no, n, outside))
    return Found(sorted(places, key=lambda p: (p.file, p.line, p.name)), False)
