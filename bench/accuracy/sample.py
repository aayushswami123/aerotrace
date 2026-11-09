"""Draw the small accuracy sample, without AeroTrace.

Usage: python3 -I bench/accuracy/sample.py <redis-root> <sqlite-root> > sample.json

Seed: items are ordered by sha256("aerotrace-bench-small-1/" + id) and taken from the top.
An item is skipped (and the skip is recorded) when its name has more than CAP mentions in the
searched files, so the hand check stays affordable. This biases the sample toward less-used names.
A function whose definition is not compiled in this build (inside a disabled #if, see live.py), a field
with more than CAP mentions and a field with no uses are skipped the same way. Every skip is recorded.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from cfuncs import blank_comments_and_strings, compiled_files, functions
from live import live_lines

SEED = "aerotrace-bench-small-1/"
CAP = 40
WANT = {"redis": (15, 5), "sqlite": (10, 5)}
STRUCTS = {
    "redis": [("src/server.h", "redisServer"), ("src/server.h", "client"), ("src/object.h", "redisObject")],
    "sqlite": [("sqlite3.c", "Parse"), ("sqlite3.c", "Vdbe"), ("sqlite3.c", "sqlite3")],
}


def h(s: str) -> str:
    return hashlib.sha256((SEED + s).encode()).hexdigest()


def searched_files(root: Path, repo: str) -> list[Path]:
    if repo == "redis":
        return sorted((root / "src").glob("*.[ch]"))
    return [root / "shell.c", root / "sqlite3.c"]


def struct_fields(path: Path, name: str) -> list[str]:
    text = blank_comments_and_strings(path.read_text(errors="replace"))
    m = re.search(r"^(?:typedef )?struct " + name + r"\s*\{", text, re.M)
    if not m:
        raise SystemExit(f"struct {name} not found in {path}")
    i, depth = m.end(), 1
    body_start = i
    while depth:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    body = text[body_start : i - 1]
    # Drop nested struct/union bodies but keep the member name after them.
    while re.search(r"\{[^{}]*\}", body):
        body = re.sub(r"\{[^{}]*\}", " ", body)
    body = re.sub(r"^\s*#.*$", "", body, flags=re.M)
    fields = []
    for decl in body.split(";"):
        decl = " ".join(decl.split())
        if not decl:
            continue
        fp = re.search(r"\(\s*\*\s*(\w+)\s*\)", decl)
        if fp:
            fields.append(fp.group(1))
            continue
        for part in decl.split(","):
            part = re.sub(r"\[.*?\]|:\s*\w+", "", part).strip()
            mm = re.search(r"(\w+)\s*$", part)
            if mm:
                fields.append(mm.group(1))
    return sorted(set(fields))


def main() -> None:
    roots = {"redis": Path(sys.argv[1]), "sqlite": Path(sys.argv[2])}
    out = {"seed": SEED, "cap": CAP, "functions": [], "fields": [], "skipped": []}
    for repo, root in roots.items():
        texts = [
            blank_comments_and_strings(p.read_text(errors="replace")) for p in searched_files(root, repo)
        ]
        big = "\n".join(texts)

        def mentions(pattern: str, big: str = big) -> int:
            return len(re.findall(pattern, big))

        n_fn, n_field = WANT[repo]
        live = live_lines(root)
        cands, dead = set(), set()
        for path in compiled_files(root):
            rel = path.relative_to(root).as_posix()
            lv = live.get(os.path.realpath(path), set())
            for f in functions(path):
                fid = f"{rel}:{f['name']}"
                if any(n in lv for n in range(f["start"], f["end"] + 1)):
                    cands.add(fid)
                else:
                    dead.add(fid)
        dead -= cands
        taken = 0
        for fid in sorted(cands | dead, key=h):
            if taken == n_fn:
                break
            if fid in dead:
                out["skipped"].append({"repo": repo, "id": fid, "reason": "definition not compiled"})
                continue
            name = fid.rsplit(":", 1)[1]
            c = mentions(r"\b" + re.escape(name) + r"\b")
            if c > CAP:
                out["skipped"].append(
                    {"repo": repo, "id": fid, "mentions": c, "reason": f"over {CAP} mentions"}
                )
                continue
            out["functions"].append({"repo": repo, "id": fid, "mentions": c})
            taken += 1
        fcands = []
        for rel, s in STRUCTS[repo]:
            fcands += [f"{s}.{f}" for f in struct_fields(root / rel, s)]
        fcands = sorted(set(fcands), key=h)
        taken = 0
        for fid in fcands:
            if taken == n_field:
                break
            field = fid.split(".", 1)[1]
            c = mentions(r"(?:\.|->)\s*" + re.escape(field) + r"\b")
            if c > CAP or c == 0:
                why = f"over {CAP} mentions" if c else "no uses"
                out["skipped"].append({"repo": repo, "id": fid, "mentions": c, "reason": why})
                continue
            out["fields"].append({"repo": repo, "id": fid, "mentions": c})
            taken += 1
    json.dump(out, sys.stdout, indent=1)
    print()


if __name__ == "__main__":
    main()
