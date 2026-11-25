"""Turn each case in cases.json into a unified diff, mechanically, from the edit its kind describes.

Usage: python3 -I bench/cases/make_patches.py <redis-root> <sqlite-root> <curl-root>
Writes bench/cases/patches/<case-id>.diff (paths relative to the repo root, a/ and b/ prefixes) and
bench/cases/patches/index.json (files and lines each patch changes). Roots must be unmodified
unpacks of the pinned archives.

Positions are found on a copy of the text with comments and strings blanked (same length, so offsets
match) and the edit is made on the real text. Does not use AeroTrace.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "accuracy"))
from cfuncs import blank_comments_and_strings, functions  # noqa: E402

PARAM = "int aerotrace_bench"
NOT_TYPES = {"return", "else", "case", "goto", "do", "sizeof", "if", "while", "for", "switch"}


def files_of(root: Path, repo: str) -> list[Path]:
    """The files of the program each repo builds (the same set bench/cases/pick.py scans)."""
    if repo == "redis":
        mk = (root / "src" / "Makefile").read_text()
        line = next(x for x in mk.splitlines() if x.startswith("REDIS_SERVER_OBJ="))
        units = [root / "src" / (o[:-2] + ".c") for o in line.split("=", 1)[1].split()]
        return sorted({*units, *(root / "src").glob("*.h"), *(root / "src").glob("*.def")})
    if repo == "sqlite":
        return [root / "shell.c", root / "sqlite3.c", root / "sqlite3.h"]
    import curl_build  # the curl program and libcurl: the same file lists as its compile database

    units = [root / "lib" / f for f in curl_build.make_list(root / "lib" / "Makefile.inc", ["CSOURCES"])]
    units += [
        root / "src" / f
        for f in curl_build.make_list(root / "src" / "Makefile.inc", ["CURL_CFILES", "CURLX_CFILES"])
    ]
    heads = [
        *(root / "lib").rglob("*.h"),
        *(root / "include" / "curl").glob("*.h"),
        *(root / "src").glob("*.h"),
    ]
    return sorted(
        {Path(os.path.normpath(u)) for u in units if Path(os.path.normpath(u)).exists()} | set(heads)
    )


def match_paren(blank: str, i: int) -> int:
    """Index of the ')' that closes the '(' at i."""
    depth = 0
    for j in range(i, len(blank)):
        if blank[j] == "(":
            depth += 1
        elif blank[j] == ")":
            depth -= 1
            if depth == 0:
                return j
    raise ValueError("unbalanced")


def line_start(text: str, i: int) -> int:
    return text.rfind("\n", 0, i) + 1


def declarations(blank: str, name: str) -> list[tuple[int, int, int, str]]:
    """(name_pos, open_paren, close_paren, 'def'|'proto') for every definition or prototype of name."""
    out = []
    for m in re.finditer(r"(?<![\w\.>])" + re.escape(name) + r"\s*\(", blank):
        before = blank[line_start(blank, m.start()) : m.start()]
        if re.match(r"\s*#", before):
            continue
        # A type must come first on the line, or, for a definition at column 0, on the line above.
        typed = re.fullmatch(r"\s*(?:[A-Za-z_]\w*[\s\*]+)*", before) and before.strip()
        if not typed and (before.strip() or m.start() != line_start(blank, m.start())):
            continue
        first = before.split()[0] if before.split() else ""
        if first in NOT_TYPES:
            continue
        op = blank.index("(", m.start())
        cl = match_paren(blank, op)
        nxt = blank[cl + 1 :].lstrip()[:1]
        if nxt == "{":
            out.append((m.start(), op, cl, "def"))
        elif nxt in (";", ","):
            if not before.strip():
                continue  # a bare `name(...);` at column 0 with no type is a call, not a prototype
            out.append((m.start(), op, cl, "proto"))
    return out


def add_param(text: str, op: int, cl: int) -> str:
    inner = text[op + 1 : cl]
    if inner.strip() in ("", "void"):
        return text[: op + 1] + PARAM + text[cl:]
    return text[:cl] + ", " + PARAM + text[cl:]


class Tree:
    """The repo's files, edited in memory."""

    def __init__(self, root: Path, repo: str):
        self.root, self.repo = root, repo
        self.orig = {p: p.read_text(errors="surrogateescape") for p in files_of(root, repo)}
        self.new = dict(self.orig)
        self._blank: dict[Path, tuple[str, str]] = {}

    def blank(self, p: Path) -> str:
        text = self.new[p]
        cached = self._blank.get(p)
        if cached is None or cached[0] is not text:
            cached = (text, blank_comments_and_strings(text))
            self._blank[p] = cached
        return cached[1]

    def diff(self) -> tuple[str, dict[str, list[int]]]:
        out, changed = [], {}
        for p in sorted(self.orig):
            if self.orig[p] == self.new[p]:
                continue
            rel = p.relative_to(self.root).as_posix()
            a, b = self.orig[p].splitlines(keepends=True), self.new[p].splitlines(keepends=True)
            out += difflib.unified_diff(a, b, f"a/{rel}", f"b/{rel}", n=3)
            lines = []
            for tag, _i1, _i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
                if tag != "equal":
                    lines += list(range(j1 + 1, max(j2, j1 + 1) + 1)) if tag != "delete" else [j1 + 1]
            changed[rel] = sorted(set(lines))
        return "".join(out), changed


def _is_static(blank: str, pos: int) -> bool:
    ls = line_start(blank, pos)
    head = blank[ls:pos] if blank[ls:pos].strip() else blank[line_start(blank, ls - 1) : pos]
    return re.search(r"\bstatic\b", head) is not None


def edit_signature(t: Tree, name: str, how: str, home: Path) -> int:
    """Apply `how` ('param' or 'void') to the definition in `home` and the prototypes that can refer to
    it: in `home` itself, and, unless it is static, every non-static prototype in other files. Other
    files' own definitions or static prototypes of the same name are different functions; left alone."""
    home_blank = t.blank(home)
    defs = [d for d in declarations(home_blank, name) if d[3] == "def"]
    if len(defs) != 1:
        raise ValueError(f"{len(defs)} definitions of {name} in {home}")
    static = _is_static(home_blank, defs[0][0])
    n = 0
    for p in t.new:
        if static and p != home:
            continue
        while True:
            blank = t.blank(p)
            todo = [
                d
                for d in declarations(blank, name)
                if not _done(t.new[p], d, how)
                and (p == home or (d[3] == "proto" and not _is_static(blank, d[0])))
            ]
            if not todo:
                break
            pos, op, cl, _ = todo[-1]  # last first, so earlier offsets stay valid
            text = t.new[p]
            text = add_param(text, op, cl) if how == "param" else _int_to_void(text, blank, pos)
            t.new[p] = text
            n += 1
    return n


def _done(text: str, d: tuple[int, int, int, str], how: str) -> bool:
    pos, op, cl, _ = d
    if how == "param":
        return PARAM in text[op:cl]
    ls = line_start(text, pos)
    head = text[ls:pos]
    if not head.strip():
        prev = line_start(text, ls - 1)
        head = text[prev:ls]
    return re.search(r"\bint\b", head) is None


def _int_to_void(text: str, blank: str, pos: int) -> str:
    ls = line_start(blank, pos)
    if not blank[ls:pos].strip():
        ls = line_start(blank, ls - 1)  # return type on the line above
    seg = blank[ls:pos]
    m = list(re.finditer(r"\bint\b", seg))
    if not m:
        raise ValueError("no int in return type")
    k = ls + m[-1].start()
    return text[:k] + "void" + text[k + 3 :]


def body_range(t: Tree, p: Path, name: str) -> tuple[int, int]:
    """Offsets of the '{' and the matching '}' of the definition of name in p."""
    blank = t.blank(p)
    for _pos, _op, cl, kind in declarations(blank, name):
        if kind == "def":
            ob = blank.index("{", cl)
            depth = 0
            for j in range(ob, len(blank)):
                if blank[j] == "{":
                    depth += 1
                elif blank[j] == "}":
                    depth -= 1
                    if depth == 0:
                        return ob, j
    raise ValueError(f"definition of {name} not found in {p}")


def returns_to_void(t: Tree, p: Path, name: str) -> int:
    n = 0
    while True:
        ob, cb = body_range(t, p, name)
        blank = t.blank(p)
        found = None
        for m in re.finditer(r"\breturn\b", blank[ob:cb]):
            s = ob + m.start()
            e = blank.index(";", s)
            expr = t.new[p][s + 6 : e].strip()
            if expr and not expr.startswith("(void)") and "aerotrace" not in t.new[p][s:e]:
                found = (s, e, expr)
        if not found:
            return n
        s, e, expr = found
        t.new[p] = (
            t.new[p][:s] + "{ (void)(" + expr + "); return; } /* aerotrace bench */" + t.new[p][e + 1 :]
        )
        n += 1


def remove_definition(t: Tree, p: Path, line: int, end: int) -> None:
    lines = t.new[p].split("\n")
    t.new[p] = "\n".join(lines[: line - 1] + lines[end:])


def struct_body(blank: str, struct: str) -> tuple[int, int]:
    m = re.search(r"^(?:typedef )?struct " + struct + r"\s*\{", blank, re.M)
    if not m:
        raise ValueError(f"struct {struct} not found")
    i, depth = m.end(), 1
    while depth:
        depth += {"{": 1, "}": -1}.get(blank[i], 0)
        i += 1
    return m.end(), i - 1


def member_pos(blank: str, start: int, end: int, field: str) -> int:
    depth = 0
    for i in range(start, end):
        c = blank[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif (
            depth == 0
            and re.match(r"(?<![\w])" + re.escape(field) + r"\b\s*[;,\[:]", blank[i:])
            and not re.match(r"\w", blank[i - 1])
        ):
            return i
    raise ValueError(f"member {field} not found")


def make(case: dict, root: Path) -> tuple[str, dict[str, list[int]], str]:
    repo, kind = case["repo"], case["kind"]
    t = Tree(root, repo)
    sym = case["symbol"]
    note = ""
    if kind in ("add_param", "ptr_param", "remove_fn", "ptr_remove", "int_to_void", "behaviour"):
        rel, name = sym.rsplit(":", 1)
        p = root / rel
        if kind in ("add_param", "ptr_param"):
            note = f"edited {edit_signature(t, name, 'param', p)} declarations"
        elif kind in ("remove_fn", "ptr_remove"):
            fn = next(f for f in functions(p) if f["name"] == name and f["start"] == case["line"])
            remove_definition(t, p, fn["start"], fn["end"])
            note = f"deleted lines {fn['start']}-{fn['end']}"
        elif kind == "int_to_void":
            n = edit_signature(t, name, "void", p)
            r = returns_to_void(t, p, name)
            note = f"edited {n} declarations, {r} return statements"
        else:
            ob, _ = body_range(t, p, name)
            ret = case.get("ret", "")
            value = "NULL" if "*" in ret else ("false" if re.search(r"\bbool\b", ret) else "0")
            t.new[p] = t.new[p][: ob + 1] + f"\n  return {value}; /* aerotrace bench */" + t.new[p][ob + 1 :]
            note = f"first statement: return {value};"
    elif kind in ("field_rename", "field_type"):
        struct, field = sym.split(".", 1)
        p = root / case["file"]
        blank = t.blank(p)
        s, e = struct_body(blank, struct)
        pos = member_pos(blank, s, e, field)
        text = t.new[p]
        if kind == "field_rename":
            t.new[p] = text[:pos] + field + "_renamed" + text[pos + len(field) :]
            note = "renamed in the struct"
        else:
            st = max(blank.rfind(";", s, pos), blank.rfind("{", s, pos), blank.rfind("}", s, pos)) + 1
            en = blank.index(";", pos)
            decl = blank[st:en]
            if "," in decl:
                raise ValueError("comma list declaration; rule: replace the case")
            lead = re.match(r"\s*", text[st:en]).group(0)
            t.new[p] = text[:st] + lead + "void *" + field + text[en:]
            note = f"was `{' '.join(decl.split())}`"
    elif kind == "global_rename":
        rel, name = sym.rsplit(":", 1)
        p = root / rel
        lines = t.new[p].split("\n")
        i = case["line"] - 1
        lines[i] = re.sub(r"\b" + re.escape(name) + r"\b", name + "_renamed", lines[i], count=1)
        t.new[p] = "\n".join(lines)
        note = "renamed at its definition"
    elif kind == "macro_arity":
        name = sym.rsplit(":", 1)[1]
        n = 0
        for p in t.new:

            def repl(m: re.Match[str]) -> str:
                inner = m.group(2)
                return (
                    m.group(1) + (inner + ", aerotrace_bench" if inner.strip() else "aerotrace_bench") + ")"
                )

            new = re.sub(
                r"(^\s*#\s*define\s+" + re.escape(name) + r"\()([^)]*)\)", repl, t.new[p], flags=re.M
            )
            n += new != t.new[p] and len(
                re.findall(r"^\s*#\s*define\s+" + re.escape(name) + r"\(", t.new[p], re.M)
            )
            t.new[p] = new
        note = f"edited {n} #defines"
    patch, changed = t.diff()
    if not patch:
        raise ValueError("empty patch")
    return patch, changed, note


def main() -> None:
    roots = {"redis": Path(sys.argv[1]), "sqlite": Path(sys.argv[2]), "curl": Path(sys.argv[3])}
    cases = json.loads((HERE / "cases.json").read_text())["cases"]
    out = HERE / "patches"
    out.mkdir(exist_ok=True)
    index = {}
    for c in cases:
        patch, changed, note = make(c, roots[c["repo"]].resolve())
        (out / f"{c['id']}.diff").write_text(patch, errors="surrogateescape")
        index[c["id"]] = {"changed": changed, "note": note}
        print(f"{c['id']:10} {c['kind']:13} {note}")
    (out / "index.json").write_text(json.dumps(index, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
