"""Draw the benchmark cases, without AeroTrace.

Usage:
  python3 -I bench/cases/curl_build.py configure <curl-root>    # once, in a temp copy of the tarball
  python3 -I bench/cases/pick.py <redis-root> <sqlite-root> <curl-root> > bench/cases/cases.json

Each repo gets the same 10 kinds of change (KINDS), fixed before any tool is run. Candidates come from
plain regex scans (bench/accuracy/cfuncs.py) and the C preprocessor (`cc -E`), never from AeroTrace.
Candidates are ordered by sha256(SEED + repo + "/" + kind + "/" + id); the first one that passes the
kind's filter is taken. A name is used by at most one case per repo. Every skipped candidate is
recorded with its reason.

The program each repo builds, and so what "compiled" means:
- Redis: `redis-server` (REDIS_SERVER_OBJ in src/Makefile), flags from tests/realrepo/repos.py.
- SQLite: the `sqlite3` shell (shell.c + sqlite3.c), default flags.
- curl: the `curl` program and libcurl, configured by bench/cases/curl_build.py (fixed options).
Only uses on lines that survive `cc -E` count as live. Macro definitions are live when `cc -E -dD`
keeps them.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "accuracy"))
sys.path.insert(0, str(HERE.parents[1] / "tests" / "realrepo"))
from cfuncs import blank_comments_and_strings, functions  # noqa: E402

SEED = "aerotrace-bench-cases-1/"
CAP = 150  # most mentions for cases whose truth comes from the compiler
CAP_HAND = 40  # "might break" cases are checked by hand

# kind -> (level, the edit, why the kind is in the benchmark). Edits change only what is said here:
# every other declaration (prototypes, `extern`s) stays as it is.
KINDS = {
    "add_param": (
        "easy",
        "add a trailing `int aerotrace_bench` parameter to the definition and every prototype of that function "
        "(same-named functions in other files are left alone); calls unchanged",
        "every direct caller stops compiling; drawn from functions with 10 to 150 direct calls (wide fan-out)",
    ),
    "remove_fn": (
        "easy",
        "delete the definition; any prototype stays",
        "callers fail to compile, or, when a prototype stays in a header, only at link",
    ),
    "int_to_void": (
        "easy",
        "change the return type from int to void in the definition and every prototype; "
        "`return X;` becomes `(void)(X); return;`",
        "only callers that use the value break; callers that ignore it still build",
    ),
    "ptr_param": (
        "hard",
        "add a trailing `int aerotrace_bench` parameter to the definition and every prototype of a function "
        "that is called directly and also stored as a pointer; calls and tables unchanged",
        "direct calls break, and so does the place it is put in a table or assigned to a pointer",
    ),
    "ptr_remove": (
        "hard",
        "delete the definition of a function that is reached only through a pointer; any prototype stays",
        "nothing calls it by name; only the table or assignment that names it breaks",
    ),
    "field_rename": (
        "hard",
        "rename the struct field to `<name>_renamed` in the struct only",
        "every reader and writer breaks; same-named fields of other structs must not be counted",
    ),
    "field_type": (
        "hard",
        "change the type of an integer struct field to `void *`",
        "arithmetic and integer uses break; some uses still build and may behave differently",
    ),
    "global_rename": (
        "hard",
        "rename the definition of a file-scope variable to `<name>_renamed`; any `extern` stays",
        "every use breaks (at compile time, or at link when an `extern` stays)",
    ),
    "macro_arity": (
        "hard",
        "add a trailing parameter `aerotrace_bench` to every `#define` of the function-like macro",
        "every use breaks; macros are a common gap for tools that read the code after the preprocessor",
    ),
    "behaviour": (
        "might_break",
        "make the function return the zero value of its type (0, NULL or false) as its first statement",
        "nothing fails to build; callers that use the value may behave differently (truth checked by hand)",
    ),
}
FN_KINDS = ("add_param", "remove_fn", "int_to_void", "ptr_param", "ptr_remove", "behaviour")

STRUCTS = {
    "redis": [("src/server.h", "redisServer"), ("src/server.h", "client"), ("src/object.h", "redisObject")],
    "sqlite": [("sqlite3.c", "Parse"), ("sqlite3.c", "Vdbe"), ("sqlite3.c", "sqlite3")],
    "curl": [
        ("lib/urldata.h", "Curl_easy"),
        ("lib/urldata.h", "connectdata"),
        ("lib/urldata.h", "UserDefined"),
    ],
}
INT_TYPES = re.compile(
    r"^(?:unsigned\s+|signed\s+)?(?:int|long|long\s+long|short|char|size_t|ssize_t|off_t|time_t|mstime_t|"
    r"u?int(?:8|16|32|64)_t|i64|u64|u32|u16|u8|int64_t|uint64_t|curl_off_t|timediff_t|unsigned)$"
)
RET_INT = re.compile(r"^(?:static\s+)?(?:inline\s+)?int\s*$")
RET_VOID = re.compile(r"\bvoid\s*$")
MARK = re.compile(r'^# (\d+) "([^"]+)"')


def h(s: str) -> str:
    return hashlib.sha256((SEED + s).encode()).hexdigest()


# ---------------------------------------------------------------- what each repo builds


def setup(root: Path, repo: str) -> list[dict]:
    """Write compile_commands.json for the program the repo builds; return its entries."""
    if repo == "curl":
        import curl_build

        return curl_build.write_compdb(root)
    import repos

    repos.write_compdb(repos.REDIS if repo == "redis" else repos.SQLITE, root)
    return json.loads((root / "compile_commands.json").read_text())


def searched_files(root: Path, repo: str, entries: list[dict]) -> list[Path]:
    units = {Path(os.path.realpath(Path(e["directory"]) / e["file"])) for e in entries}
    if repo == "redis":
        extra = [
            *(root / "src").glob("*.h"),
            *(root / "src").glob("*.def"),
        ]  # commands.def: the command table
    elif repo == "sqlite":
        extra = [root / "sqlite3.h"]
    else:
        extra = [
            *(root / "lib").rglob("*.h"),
            *(root / "include" / "curl").glob("*.h"),
            *(root / "src").glob("*.h"),
        ]
    return sorted(units | {Path(os.path.realpath(p)) for p in extra})


def _cpp(entry: dict, keep_defines: bool) -> dict[str, set[int]]:
    args = [a for a in entry["arguments"] if a != "-c"]
    if "-o" in args:
        i = args.index("-o")
        del args[i : i + 2]
    cmd = ["/usr/bin/cc", "-E", *(["-dD"] if keep_defines else []), *args[1:]]
    r = subprocess.run(cmd, cwd=entry["directory"], capture_output=True, text=True, errors="replace")
    out: dict[str, set[int]] = {}
    cur, line = None, 0
    for s in r.stdout.split("\n"):
        m = MARK.match(s)
        if m:
            line = int(m.group(1))
            cur = os.path.realpath(os.path.join(entry["directory"], m.group(2)))
            continue
        if cur and s.strip() and (not keep_defines or s.startswith("#define")):
            out.setdefault(cur, set()).add(line)
        line += 1
    return out


def preprocess(entries: list[dict], keep_defines: bool) -> dict[str, set[int]]:
    merged: dict[str, set[int]] = {}
    with ThreadPoolExecutor(8) as ex:
        for part in ex.map(lambda e: _cpp(e, keep_defines), entries):
            for k, v in part.items():
                merged.setdefault(k, set()).update(v)
    return merged


# ---------------------------------------------------------------- scanning


def struct_decls(path: Path, name: str) -> dict[str, str]:
    """field -> declared type, top-level members only. Bitfields get type 'bitfield'."""
    text = blank_comments_and_strings(path.read_text(errors="replace"))
    m = re.search(r"^(?:typedef )?struct " + name + r"\s*\{", text, re.M)
    if not m:
        raise SystemExit(f"struct {name} not found in {path}")
    i, depth = m.end(), 1
    start = i
    while depth:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    body = text[start : i - 1]
    while re.search(r"\{[^{}]*\}", body):
        body = re.sub(r"\{[^{}]*\}", " ", body)
    body = re.sub(r"^\s*#.*$", "", body, flags=re.M)
    out: dict[str, str] = {}
    for decl in body.split(";"):
        decl = " ".join(decl.split())
        if not decl or "(" in decl:
            continue
        parts = decl.split(",")
        first = re.sub(r"\[.*?\]", "", parts[0]).strip()
        mm = re.match(r"(.*?)(\**)\s*(\w+)\s*(:\s*\w+)?$", first)
        if not mm:
            continue
        typ = mm.group(1).strip()
        for part in parts:
            part = re.sub(r"\[.*?\]", "", part).strip()
            pm = re.search(r"(\**)\s*(\w+)\s*(:\s*\w+)?\s*$", part)
            if not pm:
                continue
            if pm.group(3):
                out[pm.group(2)] = "bitfield"
                continue
            stars = pm.group(1) or (mm.group(2) if part is parts[0] else "")
            out[pm.group(2)] = (typ + " " + stars).strip() if stars else typ
    return out


class Repo:
    def __init__(self, root: Path, repo: str):
        self.root, self.repo = root, repo
        entries = setup(root, repo)
        self.files = searched_files(root, repo, entries)
        self.units = sorted({Path(os.path.realpath(Path(e["directory"]) / e["file"])) for e in entries})
        self.live = preprocess(entries, keep_defines=False)
        self.live_defines = preprocess(entries, keep_defines=True)
        self.lines: dict[Path, list[str]] = {}
        self.kind: dict[Path, list[str]] = {}  # per line: "body", "define", "file"
        self.fns: dict[Path, list[dict]] = {}
        self.init: dict[Path, list[bool]] = {}
        for p in self.files:
            lines = blank_comments_and_strings(p.read_text(errors="replace")).split("\n")
            fns = functions(p) if p.suffix != ".def" else []
            k = ["file"] * len(lines)
            for f in fns:
                for n in range(f["start"], f["end"]):  # body lines: after the first line of the definition
                    k[n] = "body"
            cont = False
            for n, ln in enumerate(lines):
                if cont or re.match(r"\s*#\s*define\b", ln):
                    k[n] = "define"
                    cont = ln.rstrip().endswith("\\")
            # Initializers at file scope (`x = { ... };`): the only place outside a body where naming a
            # function stores it. A .def file is all table rows (Redis commands.def).
            init = [p.suffix == ".def"] * len(lines)
            depth = 0
            live = self.live.get(str(p), set())
            for n, ln in enumerate(lines):
                if k[n] != "file" or (n + 1) not in live:  # compiled lines only: they are balanced
                    continue
                if depth <= 0 and re.search(r"=\s*\{", ln):
                    depth = ln.count("{") - ln.count("}")
                    init[n] = True
                elif depth > 0:
                    init[n] = True
                    depth += ln.count("{") - ln.count("}")
            self.init[p] = init
            self.lines[p], self.kind[p], self.fns[p] = lines, k, fns
        self.big = "\n".join("\n".join(v) for v in self.lines.values())

    def rel(self, p: Path) -> str:
        return os.path.relpath(p, os.path.realpath(self.root))

    def is_live(self, p: Path, n: int) -> bool:
        return (n + 1) in self.live.get(str(p), set())

    def fn_uses(self, name: str, own: tuple[Path, int, int] | None) -> dict[str, int]:
        call = re.compile(r"(?<![\.\>\w])" + re.escape(name) + r"\s*\(")
        # A use as a value: in a table, an assignment, an argument or a return. A declaration with the
        # same name (`bool xattr;` in some struct) is not one.
        word = re.compile(r"(?:^|[=,{(?:]|\breturn)\s*&?\s*" + re.escape(name) + r"\s*(?=[,;})\]:]|$)")
        c = {"calls": 0, "live_calls": 0, "macro_calls": 0, "values": 0, "live_values": 0, "self_calls": 0}
        for p, lines in self.lines.items():
            kinds = self.kind[p]
            for n, ln in enumerate(lines):
                if name not in ln:
                    continue
                if own and p == own[0] and own[1] - 1 <= n < own[2]:
                    c["self_calls"] += len(call.findall(ln)) if n >= own[1] else 0
                    continue
                k = kinds[n]
                nc, nv = len(call.findall(ln)), len(word.findall(ln))
                if k == "define":
                    c["macro_calls"] += nc
                    c["values"] += nv
                elif k == "body":
                    c["calls"] += nc
                    c["values"] += nv
                    if self.is_live(p, n):
                        c["live_calls"] += nc
                        c["live_values"] += nv
                else:  # file scope: only inside an initializer (tables); anything else is a declaration
                    if nc or not self.init[p][n]:
                        continue
                    c["values"] += nv
                    if self.is_live(p, n):
                        c["live_values"] += nv
        c["mentions"] = len(re.findall(r"\b" + re.escape(name) + r"\b", self.big))
        return c

    def word_uses(self, pattern: re.Pattern[str], skip: tuple[Path, int] | None = None) -> tuple[int, int]:
        """(uses, live uses) of a regex outside #define lines; `skip` = (file, line index) of the definition."""
        n_all = n_live = 0
        for p, lines in self.lines.items():
            for n, ln in enumerate(lines):
                if skip and p == skip[0] and n == skip[1]:
                    continue
                if self.kind[p][n] == "define":
                    continue
                k = len(pattern.findall(ln))
                n_all += k
                if k and self.is_live(p, n):
                    n_live += k
        return n_all, n_live


def candidates(r: Repo) -> dict[str, list[dict]]:
    defs = []
    for p in r.units:
        if p not in r.lines:
            continue
        lines = r.lines[p]
        for f in r.fns[p]:
            head = lines[f["start"] - 1]
            before = head[: head.find(f["name"])]
            if not before.strip() and f["start"] >= 2:
                before = lines[f["start"] - 2]  # return type on the line above
            live = any(r.is_live(p, n - 1) for n in range(f["start"], f["end"] + 1))
            defs.append(
                {
                    "id": f"{r.rel(p)}:{f['name']}",
                    "name": f["name"],
                    "file": r.rel(p),
                    "line": f["start"],
                    "end": f["end"],
                    "ret": " ".join(before.split()),
                    "live": live,
                    "_p": p,
                }
            )
    fn_names = {d["name"] for d in defs}
    for lines in r.lines.values():  # prototypes too, so a macro never shares a function's name
        for ln in lines:
            m = re.match(r"^[A-Za-z_][\w \t\*]*?\b([A-Za-z_]\w*)\s*\(", ln)
            if m and ln.rstrip().endswith((";", ",")):
                fn_names.add(m.group(1))

    fields = []
    for rel, s in STRUCTS[r.repo]:
        for fname, typ in struct_decls(r.root / rel, s).items():
            fields.append({"id": f"{s}.{fname}", "struct": s, "name": fname, "type": typ, "file": rel})

    gvar = re.compile(
        r"^(?!typedef\b|extern\b|return\b|struct\s+\w+\s*\{|enum\b|union\b)(?:static\s+)?(?:const\s+)?"
        r"[A-Za-z_][\w \t\*]*?[ \t\*]\**([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*(?:=|;)"
    )
    globals_ = []
    for p in r.units:
        if p not in r.lines:
            continue
        depth = 0
        for n, ln in enumerate(r.lines[p]):
            k = r.kind[p][n]
            if k != "define" and depth == 0:
                m = gvar.match(ln)
                if m and "(" not in ln.split("=")[0] and r.is_live(p, n):
                    globals_.append(
                        {
                            "id": f"{r.rel(p)}:{m.group(1)}",
                            "name": m.group(1),
                            "file": r.rel(p),
                            "line": n + 1,
                            "_p": p,
                            "_n": n,
                        }
                    )
            # Depth over every compiled line (bodies too): compiled code is balanced, while braces on
            # lines the preprocessor drops (extern "C" {, #if/#else halves) would throw the count off.
            if k != "define" and r.is_live(p, n):
                depth = max(depth + ln.count("{") - ln.count("}"), 0)

    define_re = re.compile(r"^\s*#\s*define\s+([A-Za-z_]\w*)\(([^)]*)\)")
    all_defines: dict[str, list[tuple[Path, int, str]]] = {}
    for p, lines in r.lines.items():
        for n, ln in enumerate(lines):
            m = define_re.match(ln)
            if m:
                all_defines.setdefault(m.group(1), []).append((p, n, m.group(2)))
    macros = []
    for name, where in sorted(all_defines.items()):
        p, n, params = where[0]
        if "..." in params:
            continue
        live_defs = sum(1 for q, i, _ in where if (i + 1) in r.live_defines.get(str(q), set()))
        macros.append(
            {
                "id": f"{r.rel(p)}:{name}",
                "name": name,
                "file": r.rel(p),
                "line": n + 1,
                "live_define": live_defs,
                "defines": len(where),
                "is_function": name in fn_names,
            }
        )
    return {"defs": defs, "fields": fields, "globals": globals_, "macros": macros}


def pick(repo: str, root: Path) -> tuple[list[dict], list[dict], dict]:
    r = Repo(root, repo)
    pools = candidates(r)
    taken: set[str] = set()
    cases, skipped = [], []

    def fn_filter(kind: str, d: dict) -> str | None:
        if d["name"] == "main":
            return "main"
        if not d["live"]:
            return "definition not compiled"
        u = r.fn_uses(d["name"], (d["_p"], d["line"], d["end"]))
        d["counts"] = u
        cap = CAP_HAND if kind == "behaviour" else CAP
        if u["mentions"] > cap:
            return f"over {cap} mentions ({u['mentions']})"
        direct = u["calls"] + u["macro_calls"]
        if kind in ("add_param", "remove_fn", "int_to_void", "behaviour"):
            if u["values"]:
                return f"also used as a value ({u['values']})"
            lo, hi = (10, 150) if kind == "add_param" else (2, 30)
            if not lo <= direct <= hi:
                return f"{direct} direct calls (want {lo} to {hi})"
            if not u["live_calls"]:
                return "no direct call in compiled code"
        if kind == "int_to_void" and not RET_INT.match(d["ret"]):
            return f"returns {d['ret'] or '?'!r}, not int"
        if kind == "behaviour" and (RET_VOID.search(d["ret"]) or not d["ret"]):
            return f"returns {d['ret'] or '?'!r} (want a value)"
        if kind == "ptr_param" and not (u["live_values"] >= 1 and u["live_calls"] >= 1):
            return f"{u['live_values']} live value uses, {u['live_calls']} live calls (want >= 1 each)"
        if kind == "ptr_remove" and not (u["live_values"] >= 1 and direct == 0):
            return f"{u['live_values']} live value uses, {direct} direct calls (want >= 1 and 0)"
        return None

    def other_filter(kind: str, c: dict) -> str | None:
        if kind in ("field_rename", "field_type"):
            n, live = r.word_uses(re.compile(r"(?:\.|->)\s*" + re.escape(c["name"]) + r"\b"))
            c["counts"] = {"uses": n, "live_uses": live}
            if c["type"] == "bitfield":
                return "bitfield"
            if not 3 <= n <= CAP:
                return f"{n} uses (want 3 to {CAP})"
            if not live:
                return "no use in compiled code"
            if kind == "field_type" and not INT_TYPES.match(c["type"]):
                return f"type {c['type']!r} is not a plain integer"
            return None
        if kind == "global_rename":
            n, live = r.word_uses(
                re.compile(r"(?<![\.\>\w])" + re.escape(c["name"]) + r"\b"), (c["_p"], c["_n"])
            )
            c["counts"] = {"uses": n, "live_uses": live}
            if not 2 <= n <= CAP:
                return f"{n} uses (want 2 to {CAP})"
            return None if live else "no use in compiled code"
        n, live = r.word_uses(re.compile(r"(?<![\.\>\w])" + re.escape(c["name"]) + r"\s*\("))
        c["counts"] = {
            "uses": n,
            "live_uses": live,
            "defines": c["defines"],
            "live_defines": c["live_define"],
        }
        if c["is_function"]:
            return "a function has the same name"
        if not c["live_define"]:
            return "no #define of it is compiled"
        if not 2 <= n <= CAP:
            return f"{n} uses (want 2 to {CAP})"
        return None if live else "no use in compiled code"

    for kind in KINDS:
        key = (
            "defs"
            if kind in FN_KINDS
            else {"global_rename": "globals", "macro_arity": "macros"}.get(kind, "fields")
        )
        chosen = None
        for c in sorted(pools[key], key=lambda c: h(f"{repo}/{kind}/{c['id']}")):
            if c["name"] in taken:
                skipped.append(
                    {"repo": repo, "kind": kind, "id": c["id"], "reason": "already used by another case"}
                )
                continue
            why = fn_filter(kind, c) if kind in FN_KINDS else other_filter(kind, c)
            if why:
                skipped.append({"repo": repo, "kind": kind, "id": c["id"], "reason": why})
                continue
            chosen = c
            break
        if chosen is None:
            raise SystemExit(f"{repo}/{kind}: no candidate passed")
        taken.add(chosen["name"])
        level, edit, why_kind = KINDS[kind]
        case = {
            "id": f"{repo}-{len(cases) + 1:02d}",
            "repo": repo,
            "kind": kind,
            "difficulty": level,
            "symbol": chosen["id"],
            "edit": edit,
            "why_this_kind": why_kind,
        }
        for k in ("file", "line", "type", "ret", "counts"):
            if k in chosen:
                case[k] = chosen[k]
        cases.append(case)
    return cases, skipped, {k: len(v) for k, v in pools.items()}


def main() -> None:
    roots = {"redis": Path(sys.argv[1]), "sqlite": Path(sys.argv[2]), "curl": Path(sys.argv[3])}
    out = {
        "seed": SEED,
        "cap": CAP,
        "cap_hand": CAP_HAND,
        "kinds": {k: {"difficulty": v[0], "edit": v[1], "why": v[2]} for k, v in KINDS.items()},
        "cases": [],
        "pools": {},
        "skipped": [],
    }
    for repo, root in roots.items():
        cases, skipped, pools = pick(repo, root.resolve())
        out["cases"] += cases
        out["skipped"] += skipped
        out["pools"][repo] = pools
    json.dump(out, sys.stdout, indent=1)
    print()


if __name__ == "__main__":
    main()
