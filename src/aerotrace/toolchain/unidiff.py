"""Read unified diffs: `git diff` output, `diff -u` output, or a patch an agent wrote.

Only line numbers and the old text are kept; nothing here knows about functions.
"""

from __future__ import annotations

import codecs
from dataclasses import dataclass, field


@dataclass
class FileDiff:
    """One file in a unified diff. Line numbers are 1-based.

    `removed` holds (old line, new gap) and `added` holds (new line, old gap). A gap `g` on one side means
    "between line g and line g+1 on that side", which is where the other side's line was removed or added.
    """

    old_path: str | None  # None when the file is new
    new_path: str | None  # None when the file was deleted
    binary: bool = False
    removed: list[tuple[int, int]] = field(default_factory=list)
    added: list[tuple[int, int]] = field(default_factory=list)
    old_text: dict[int, str] = field(default_factory=dict)  # old lines the diff shows (context + removed)
    new_text: dict[int, str] = field(default_factory=dict)  # added lines
    hunks: int = 0


def _unquote(path: str) -> str:
    """Git quotes unusual paths in C style: "a/caf\\303\\251.c"."""
    if len(path) >= 2 and path[0] == '"' and path[-1] == '"':
        raw = codecs.escape_decode(path[1:-1].encode("utf-8", "surrogateescape"))[0]
        return raw.decode("utf-8", "surrogateescape")
    return path


def _header_path(rest: str, prefix: str) -> str | None:
    """The path on a `--- ` / `+++ ` line, without the a/ b/ prefix and any trailing timestamp."""
    rest = rest.rstrip("\r")
    if not rest.startswith('"'):
        rest = rest.split("\t", 1)[0]
    p = _unquote(rest.strip())
    if p == "/dev/null":
        return None
    return p[len(prefix) :] if p.startswith(prefix) else p


def _hunk_header(line: str) -> tuple[int, int, int, int]:
    """'@@ -a,b +c,d @@ ...' -> (a, b, c, d). A missing count means 1."""
    try:
        old, new = line.split("@@")[1].split()
        a, _, b = old[1:].partition(",")
        c, _, d = new[1:].partition(",")
        return int(a), int(b or 1), int(c), int(d or 1)
    except (IndexError, ValueError) as e:
        raise ValueError(f"bad hunk header: {line[:80]}") from e


def parse_unified(text: str) -> list[FileDiff]:
    """Parse `git diff` output or any unified diff (`diff -u`, a patch an agent wrote)."""
    files: list[FileDiff] = []
    cur: FileDiff | None = None
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    old_left = new_left = 0  # lines still expected in the current hunk
    old_ln = new_ln = 0  # the next line number on each side
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if old_left > 0 or new_left > 0:
            assert cur is not None
            tag, body = (line[:1], line[1:]) if line else (" ", "")
            if tag == "\\":  # "\ No newline at end of file"
                continue
            if tag == " ":
                cur.old_text[old_ln] = body
                old_ln, new_ln = old_ln + 1, new_ln + 1
                old_left, new_left = old_left - 1, new_left - 1
            elif tag == "-":
                cur.old_text[old_ln] = body
                cur.removed.append((old_ln, new_ln - 1))
                old_ln, old_left = old_ln + 1, old_left - 1
            elif tag == "+":
                cur.added.append((new_ln, old_ln - 1))
                cur.new_text[new_ln] = body
                new_ln, new_left = new_ln + 1, new_left - 1
            else:
                raise ValueError(f"unexpected line inside a hunk: {line[:80]}")
            continue
        if line.startswith("diff --git "):
            cur = FileDiff(None, None)
            files.append(cur)
            parts = line[len("diff --git ") :]
            if (
                not parts.startswith('"') and " b/" in parts
            ):  # fallback when there is no ---/+++ (binary, mode-only)
                a, b = parts.split(" b/", 1)
                cur.old_path = a[2:] if a.startswith("a/") else a
                cur.new_path = b
        elif line.startswith("rename from ") and cur is not None:
            cur.old_path = _unquote(line[len("rename from ") :].rstrip("\r"))
        elif line.startswith("rename to ") and cur is not None:
            cur.new_path = _unquote(line[len("rename to ") :].rstrip("\r"))
        elif line.startswith("new file mode") and cur is not None:
            cur.old_path = None
        elif line.startswith("deleted file mode") and cur is not None:
            cur.new_path = None
        elif line.startswith("Binary files ") or line.startswith("GIT binary patch"):
            if cur is not None:
                cur.binary = True
        elif line.startswith("--- ") and i < len(lines) and lines[i].startswith("+++ "):
            if cur is None or cur.hunks:  # a plain `diff -u` file, without a `diff --git` line
                cur = FileDiff(None, None)
                files.append(cur)
            cur.old_path = _header_path(line[4:], "a/")
            cur.new_path = _header_path(lines[i][4:], "b/")
            i += 1
        elif line.startswith("@@ "):
            if cur is None:
                raise ValueError("a hunk appears before any file header")
            a, b, c, d = _hunk_header(line)
            cur.hunks += 1
            old_left, new_left = b, d
            old_ln = a if b > 0 else a + 1
            new_ln = c if d > 0 else c + 1
    if old_left > 0 or new_left > 0:
        raise ValueError("the diff ends in the middle of a hunk (it may be cut off)")
    return files


def whole_file_added(path: str, n_lines: int) -> FileDiff:
    """A new file git does not track yet, as if a diff added every line."""
    return FileDiff(None, path, added=[(n, 0) for n in range(1, n_lines + 1)], hunks=1)
