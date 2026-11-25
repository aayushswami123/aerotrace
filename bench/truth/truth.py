"""Build the truth for each benchmark case: apply its patch to a built copy of the pinned repo, rebuild,
and record every compile and link error. Truth comes from the compiler and the linker, never from
AeroTrace or any other tool being compared.

Usage:
  python3 -I bench/truth/truth.py <work-dir> [case-id ...] > bench/results/<run>/truth.json

<work-dir> holds <repo>/base: an unpacked, configured and fully built copy of each pinned release (see
bench/truth/README.md for the exact commands). Each case is built in a clone (`cp -c`, copy-on-write)
that is deleted afterwards. Nothing is built in anyone's own source tree.

A "site" is where a break shows up: the function that contains the error line (`file:function`), or,
outside any function, the file-scope variable being initialized (`file:@name`). Sites are named the
same way for every tool, so answers can be compared. Errors on lines the patch itself changed are part
of the change, not breaks, and are listed apart.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CASES = HERE.parent / "cases"
sys.path.insert(0, str(HERE.parent / "accuracy"))
sys.path.insert(0, str(HERE))
from cfuncs import functions  # noqa: E402
from sites import Sites  # noqa: E402

ERR = re.compile(r"^(?P<file>[^\s:][^:\n]*):(?P<line>\d+):(?P<col>\d+): (?:fatal )?error: (?P<msg>.*)$", re.M)
UNDEF_HEAD = re.compile(r"^Undefined symbols for architecture \w+:\s*$", re.M)
UNDEF_SYM = re.compile(r'^\s+"_(?P<sym>[^"]+)", referenced from:\s*$')
UNDEF_REF = re.compile(r"^\s+_(?P<fn>[\w.$]+) in (?P<obj>\S+)\s*$")
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def build_cmd(repo: str, work: Path) -> tuple[list[str], str]:
    """(command, subdir) that rebuilds the repo's program after a change, keeping going after errors."""
    if repo == "redis":
        return ["make", "-k", "-j1", "MALLOC=libc", "CFLAGS=-ferror-limit=0", "redis-server"], "src"
    if repo == "sqlite":
        sh = (
            "/usr/bin/cc -ferror-limit=0 -c sqlite3.c -o sqlite3.o; a=$?; "
            "/usr/bin/cc -ferror-limit=0 -c shell.c -o shell.o; b=$?; "
            'if [ $a -eq 0 ] && [ $b -eq 0 ]; then /usr/bin/cc shell.o sqlite3.o -o sqlite3; else echo "LINK SKIPPED"; fi'
        )
        return ["/bin/sh", "-c", sh], "."
    cflags = (work / "curl" / "cflags.txt").read_text().strip()
    return ["make", "-k", "-j1", f"CFLAGS={cflags}"], "."


SEARCH_DIRS = {
    "redis": ["src", "."],
    "sqlite": ["."],
    "curl": ["lib", "src", "lib/vauth", "lib/vtls", "lib/curlx", "."],
}


def resolve(repo: str, tree: Path, name: str, cwd: Path) -> str | None:
    """Repo-relative path of a file named in a compiler message."""
    cands = [cwd / name] + [tree / d / name for d in SEARCH_DIRS[repo]]
    for c in cands:
        if c.exists():
            return os.path.relpath(os.path.realpath(c), os.path.realpath(tree))
    return None


_sites: dict[Path, Sites] = {}


def site_of(tree: Path, rel: str, line: int) -> str:
    if tree not in _sites:
        _sites[tree] = Sites(tree)
    return _sites[tree].site(rel, line)


def definer(repo: str, tree: Path, name: str) -> list[str]:
    """Sites that define `name` (a function or a file-scope variable), for link errors whose object is
    not a real file (LTO reports `lto.o`)."""
    out = set()
    for d in SEARCH_DIRS[repo]:
        for c in [*(tree / d).glob("*.c"), *(tree / d).glob("*.def")]:
            text = c.read_text(errors="replace")
            if name not in text:
                continue
            rel = os.path.relpath(os.path.realpath(c), os.path.realpath(tree))
            if c.suffix == ".c" and any(f["name"] == name for f in functions(c)):
                out.add(f"{rel}:{name}")
            elif re.search(
                r"^[A-Za-z_][\w \t\*]*\b" + re.escape(name) + r"\s*(?:\[[^\]]*\]\s*)*=", text, re.M
            ):
                out.add(f"{rel}:@{name}")
    return sorted(out)


def obj_to_source(repo: str, tree: Path, obj: str) -> list[str]:
    stem = Path(obj).name.rsplit(".", 1)[0]
    for prefix in ("libcurl_la-", "curl-", "libcurltool_la-"):
        stem = stem.removeprefix(prefix)
    hits = []
    for d in SEARCH_DIRS[repo]:
        c = tree / d / (stem + ".c")
        if c.exists():
            hits.append(os.path.relpath(os.path.realpath(c), os.path.realpath(tree)))
    return sorted(set(hits))


def parse(repo: str, tree: Path, cwd: Path, out: str, changed: dict[str, list[int]]) -> dict:
    out = ANSI.sub("", out)
    errors, part_of_change, unresolved = [], [], []
    seen = set()
    for m in ERR.finditer(out):
        rel = resolve(repo, tree, m.group("file"), cwd)
        line = int(m.group("line"))
        key = (rel or m.group("file"), line, m.group("msg"))
        if key in seen:
            continue
        seen.add(key)
        if rel is None:
            unresolved.append({"file": m.group("file"), "line": line, "msg": m.group("msg")})
            continue
        e = {"file": rel, "line": line, "msg": m.group("msg"), "site": site_of(tree, rel, line)}
        (part_of_change if line in changed.get(rel, []) else errors).append(e)
    links = []
    lines = out.split("\n")
    for i, ln in enumerate(lines):
        if not UNDEF_HEAD.match(ln):
            continue
        sym = None
        for nxt in lines[i + 1 :]:
            ms, mr = UNDEF_SYM.match(nxt), UNDEF_REF.match(nxt)
            if ms:
                sym = ms.group("sym")
            elif mr and sym:
                srcs = obj_to_source(repo, tree, mr.group("obj"))
                sites = [f"{s}:{mr.group('fn')}" for s in srcs] or definer(repo, tree, mr.group("fn"))
                links.append(
                    {"symbol": sym, "function": mr.group("fn"), "object": mr.group("obj"), "sites": sites}
                )
            elif nxt.strip().startswith("ld:") or not nxt.startswith(" "):
                break
    return {
        "errors": sorted(errors, key=lambda e: (e["file"], e["line"], e["msg"])),
        "part_of_change": part_of_change,
        "unresolved": unresolved,
        "link_errors": sorted(links, key=lambda x: (x["symbol"], x["function"], x["object"])),
        "link_checked": "LINK SKIPPED" not in out and not (errors or part_of_change),
    }


def run_case(case: dict, work: Path) -> dict:
    repo = case["repo"]
    base = work / repo / "base"
    tree = work / repo / f"case-{case['id']}"
    if tree.exists():
        shutil.rmtree(tree)
    subprocess.run(["cp", "-cpR", str(base), str(tree)], check=True)
    try:
        patch = CASES / "patches" / f"{case['id']}.diff"
        r = subprocess.run(["patch", "-p1", "-s", "-i", str(patch)], cwd=tree, capture_output=True, text=True)
        if r.returncode:
            return {"id": case["id"], "error": "patch did not apply: " + r.stdout + r.stderr}
        cmd, sub = build_cmd(repo, work)
        t = time.perf_counter()
        b = subprocess.run(cmd, cwd=tree / sub, capture_output=True, text=True, errors="replace")
        secs = time.perf_counter() - t
        changed = json.loads((CASES / "patches" / "index.json").read_text())[case["id"]]["changed"]
        res = parse(repo, tree, tree / sub, b.stdout + "\n" + b.stderr, changed)
        sites = sorted(
            {e["site"] for e in res["errors"]} | {s for x in res["link_errors"] for s in x["sites"]}
        )
        (work / "logs").mkdir(exist_ok=True)
        (work / "logs" / f"{case['id']}.log").write_text(ANSI.sub("", b.stdout + "\n" + b.stderr))
        return {
            "id": case["id"],
            "repo": repo,
            "kind": case["kind"],
            "difficulty": case["difficulty"],
            "symbol": case["symbol"],
            "build_exit": b.returncode,
            "build_seconds": round(secs, 1),
            "break_sites": sites,
            **res,
        }
    finally:
        shutil.rmtree(tree, ignore_errors=True)


def tool_versions() -> dict:
    def first(cmd: list[str]) -> str:
        try:
            return subprocess.run(cmd, capture_output=True, text=True).stdout.splitlines()[0].strip()
        except (OSError, IndexError):
            return "unknown"

    return {
        "cc": first(["/usr/bin/cc", "--version"]),
        "make": first(["make", "--version"]),
        "macos": platform.mac_ver()[0],
        "machine": platform.machine(),
        "cpu": first(["sysctl", "-n", "machdep.cpu.brand_string"]),
    }


def main() -> None:
    work = Path(sys.argv[1]).resolve()
    want = set(sys.argv[2:])
    cases = [
        c for c in json.loads((CASES / "cases.json").read_text())["cases"] if not want or c["id"] in want
    ]
    out = {"setup": tool_versions(), "cases": []}
    for c in cases:
        r = run_case(c, work)
        out["cases"].append(r)
        n = len(r.get("break_sites", []))
        print(
            f"{c['id']:10} {c['kind']:13} {n:4} break sites  {r.get('build_seconds', '?')} s", file=sys.stderr
        )
    json.dump(out, sys.stdout, indent=1, sort_keys=True)
    print()


if __name__ == "__main__":
    main()
