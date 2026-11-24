"""Draw the benchmark cases, without AeroTrace.

Usage: python3 -I bench/cases/pick.py <redis-root> <sqlite-root> <curl-root> > bench/cases/cases.json

Each repo gets the same 10 kinds of change (KINDS below), fixed before any tool is run. For each kind,
candidates come from plain regex scans of the source (bench/accuracy/cfuncs.py), never from AeroTrace.
Candidates are ordered by sha256(SEED + repo + "/" + kind + "/" + id) and the first one that passes
the kind's filter is taken. A symbol is used by at most one case per repo. Every candidate skipped on
the way is recorded with its reason, so the draw can be checked by hand.

The filters keep cases checkable by hand (at most CAP mentions) and make each kind mean what it says
(for example "direct calls only" really has no uses as a value). Redis and SQLite use only code that
the default build compiles (checked with cc -E, see bench/accuracy/live.py). curl's default build needs
its configure step, which has not been run yet, so curl cases use files in lib/ that every build
compiles; each one is confirmed after configure, and a case that turns out not compiled is replaced by
the next candidate in seed order (the rule is fixed here, before the run).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "accuracy"))
sys.path.insert(0, str(HERE.parents[1] / "tests" / "realrepo"))
from cfuncs import blank_comments_and_strings, functions  # noqa: E402

SEED = "aerotrace-bench-cases-1/"
CAP = 40

# kind -> (difficulty, what the edit is, why the kind is in the benchmark)
KINDS = {
    "add_param": (
        "easy",
        "add an int parameter to the definition and every prototype; call sites unchanged",
        "direct callers stop compiling; the classic signature change",
    ),
    "remove_fn": (
        "easy",
        "delete the definition (prototypes in headers stay)",
        "callers fail to compile, or, when a prototype stays in a header, only at link",
    ),
    "int_to_void": (
        "easy",
        "change the return type from int to void (definition, prototypes, return statements)",
        "only callers that use the value break; callers that ignore it still build",
    ),
    "ptr_param": (
        "hard",
        "add an int parameter to a function that is also stored as a pointer",
        "the break is where the function is put in a table or assigned, not at a call",
    ),
    "ptr_remove": (
        "hard",
        "delete a function that is reached only through a pointer (no direct calls)",
        "nothing calls it by name; only the table or assignment that names it breaks",
    ),
    "field_rename": (
        "hard",
        "rename a struct field (add the suffix _renamed)",
        "every reader and writer breaks; grep must tell this field from same-named ones",
    ),
    "field_type": (
        "hard",
        "change an integer field to a pointer type",
        "arithmetic and integer uses break, some uses still build and may behave differently",
    ),
    "global_rename": (
        "hard",
        "rename a file-scope variable",
        "every use breaks; globals are often used far from where they are defined",
    ),
    "macro_arity": (
        "hard",
        "add a parameter to a function-like macro",
        "every use of the macro breaks; macros are a common gap for tools that read the code after the preprocessor",
    ),
    "behaviour": (
        "might_break",
        "change what the function returns in one case; signature unchanged",
        "nothing fails to build; callers that use the value may behave differently",
    ),
}

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


def h(s: str) -> str:
    return hashlib.sha256((SEED + s).encode()).hexdigest()


def searched_files(root: Path, repo: str) -> list[Path]:
    if repo == "redis":
        return sorted((root / "src").glob("*.[ch]"))
    if repo == "sqlite":
        return [root / "shell.c", root / "sqlite3.c", root / "sqlite3.h"]
    return sorted(
        [
            *(root / "lib").glob("*.[ch]"),
            *(root / "include" / "curl").glob("*.h"),
            *(root / "src").glob("*.[ch]"),
        ]
    )


def def_files(root: Path, repo: str) -> list[Path]:
    """Files whose definitions can be picked."""
    if repo == "redis":
        mk = (root / "src" / "Makefile").read_text()
        line = next(x for x in mk.splitlines() if x.startswith("REDIS_SERVER_OBJ="))
        return sorted(root / "src" / (o[:-2] + ".c") for o in line.split("=", 1)[1].split())
    if repo == "sqlite":
        return [root / "sqlite3.c"]
    return sorted((root / "lib").glob("*.c"))


def live_map(root: Path, repo: str) -> dict[str, set[int]] | None:
    if repo == "curl":
        return None  # needs configure; confirmed in the truth step (see module docstring)
    import repos  # tests/realrepo/repos.py: writes compile_commands.json the way the golden tests do
    from live import live_lines

    repos.write_compdb(repos.REDIS if repo == "redis" else repos.SQLITE, root)
    return live_lines(root)


def struct_decls(path: Path, name: str) -> dict[str, str]:
    """field -> its declared type (text before the name), top-level members only."""
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
        first = re.sub(r"\[.*?\]|:\s*\w+", "", parts[0]).strip()
        mm = re.match(r"(.*?)(\**)\s*(\w+)$", first)
        if not mm:
            continue
        typ = mm.group(1).strip()
        for part in parts:
            part = re.sub(r"\[.*?\]|:\s*\w+", "", part).strip()
            pm = re.search(r"(\**)\s*(\w+)\s*$", part)
            if pm:
                stars = pm.group(1) or (mm.group(2) if part is parts[0] else "")
                out[pm.group(2)] = (typ + " " + stars).strip() if stars else typ
    return out


def scan(root: Path, repo: str) -> dict:
    texts = {p: blank_comments_and_strings(p.read_text(errors="replace")) for p in searched_files(root, repo)}
    lines_of = {p: t.split("\n") for p, t in texts.items()}
    live = live_map(root, repo)

    # function definitions, with their return type and live state
    defs = []
    for p in def_files(root, repo):
        rel = p.relative_to(root).as_posix()
        lines = lines_of.get(p) or blank_comments_and_strings(p.read_text(errors="replace")).split("\n")
        lv = None if live is None else live.get(os.path.realpath(p), set())
        for f in functions(p):
            head = lines[f["start"] - 1]
            before = head[: head.find(f["name"])]
            if not before.strip() and f["start"] >= 2:
                before = lines[f["start"] - 2]  # return type on the line above (Redis and curl style)
            live_ok = None if lv is None else any(n in lv for n in range(f["start"], f["end"] + 1))
            defs.append(
                {
                    "id": f"{rel}:{f['name']}",
                    "name": f["name"],
                    "file": rel,
                    "line": f["start"],
                    "end": f["end"],
                    "ret": " ".join(before.split()),
                    "live": live_ok,
                }
            )

    def bodies() -> list[tuple[Path, int, int]]:
        out = []
        for p in texts:
            for f in functions(p):
                out.append((p, f["start"], f["end"]))
        return out

    spans = bodies()

    def uses(name: str) -> dict[str, int]:
        call = re.compile(r"\b" + re.escape(name) + r"\s*\(")
        word = re.compile(r"(?<![\.\>\w])" + re.escape(name) + r"\b(?!\s*\()")
        n_call = n_val = n_all = 0
        for p, s, e in spans:
            for ln in lines_of[p][s:e]:  # body lines, not the definition's own first line
                n_call += len(call.findall(ln))
                n_val += len(word.findall(ln))
        for p, lines in lines_of.items():  # values at file scope: tables like {"get", getCommand, ...}
            in_fn = set()
            for pp, s, e in spans:
                if pp == p:
                    in_fn.update(range(s - 1, e))
            for i, ln in enumerate(lines):
                if i in in_fn or ln.lstrip().startswith("#"):
                    continue
                if re.search(r"\b" + re.escape(name) + r"\s*\(", ln):
                    continue  # a prototype
                n_val += len(word.findall(ln))
        for t in texts.values():
            n_all += len(re.findall(r"\b" + re.escape(name) + r"\b", t))
        return {"calls": n_call, "values": n_val, "mentions": n_all}

    # fields
    fields = []
    for rel, s in STRUCTS[repo]:
        for fname, typ in struct_decls(root / rel, s).items():
            fields.append({"id": f"{s}.{fname}", "struct": s, "name": fname, "type": typ, "file": rel})

    # file-scope variable definitions in compiled files
    gvar = re.compile(
        r"^(?!typedef\b|extern\b|return\b|struct\s+\w+\s*\{|enum\b|union\b)(?:static\s+)?(?:const\s+)?"
        r"[A-Za-z_][\w \t\*]*?[ \t\*]\**([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*(?:=|;)"
    )
    globals_ = []
    for p in def_files(root, repo):
        rel = p.relative_to(root).as_posix()
        lines = lines_of.get(p) or []
        in_fn = set()
        for pp, s, e in spans:
            if pp == p:
                in_fn.update(range(s - 1, e))
        lv = None if live is None else live.get(os.path.realpath(p), set())
        depth = 0
        for i, ln in enumerate(lines):
            if i not in in_fn and depth == 0:
                m = gvar.match(ln)
                if m and "(" not in ln.split("=")[0] and (lv is None or (i + 1) in lv):
                    globals_.append(
                        {"id": f"{rel}:{m.group(1)}", "name": m.group(1), "file": rel, "line": i + 1}
                    )
            if i not in in_fn:
                depth += ln.count("{") - ln.count("}")
                depth = max(depth, 0)

    # function-like macros
    macros = []
    seen = set()
    for p, lines in lines_of.items():
        rel = p.relative_to(root).as_posix()
        for i, ln in enumerate(lines):
            m = re.match(r"^\s*#\s*define\s+([A-Za-z_]\w*)\(([^)]*)\)", ln)
            if m and "..." not in m.group(2) and m.group(1) not in seen:
                seen.add(m.group(1))
                macros.append(
                    {
                        "id": f"{rel}:{m.group(1)}",
                        "name": m.group(1),
                        "file": rel,
                        "line": i + 1,
                        "params": len([x for x in m.group(2).split(",") if x.strip()]),
                    }
                )
    return {
        "defs": defs,
        "fields": fields,
        "globals": globals_,
        "macros": macros,
        "uses": uses,
        "texts": texts,
    }


def pick(repo: str, root: Path) -> tuple[list[dict], list[dict], dict]:
    s = scan(root, repo)
    uses = s["uses"]
    big = "\n".join(s["texts"].values())
    taken_names: set[str] = set()
    cases, skipped, pools = [], [], {}

    def field_mentions(name: str) -> int:
        return len(re.findall(r"(?:\.|->)\s*" + re.escape(name) + r"\b", big))

    def macro_uses(name: str) -> int:
        n = 0
        for t in s["texts"].values():
            for ln in t.split("\n"):
                if not ln.lstrip().startswith("#"):
                    n += len(re.findall(r"\b" + re.escape(name) + r"\s*\(", ln))
        return n

    def fn_filter(kind: str, d: dict) -> str | None:
        if d["name"] == "main":
            return "main"
        if d["live"] is False:
            return "definition not compiled"
        u = uses(d["name"])
        d["uses"] = u
        if u["mentions"] > CAP:
            return f"over {CAP} mentions ({u['mentions']})"
        if kind in ("add_param", "remove_fn", "int_to_void", "behaviour"):
            if u["values"]:
                return f"also used as a value ({u['values']})"
            if not 2 <= u["calls"] <= 30:
                return f"{u['calls']} direct calls (want 2 to 30)"
        if kind == "int_to_void" and not RET_INT.match(d["ret"]):
            return f"returns {d['ret'] or '?'!r}, not int"
        if kind == "behaviour" and (RET_VOID.search(d["ret"]) or not d["ret"]):
            return f"returns {d['ret'] or '?'!r} (want a value)"
        if kind == "ptr_param" and not (u["values"] >= 1 and u["calls"] >= 0):
            return "never used as a value"
        if kind == "ptr_remove" and not (u["values"] >= 1 and u["calls"] == 0):
            return f"{u['values']} value uses, {u['calls']} direct calls (want >= 1 and 0)"
        return None

    for kind in KINDS:
        if kind in ("add_param", "remove_fn", "int_to_void", "ptr_param", "ptr_remove", "behaviour"):
            pool = s["defs"]
        elif kind in ("field_rename", "field_type"):
            pool = s["fields"]
        elif kind == "global_rename":
            pool = s["globals"]
        else:
            pool = s["macros"]
        pools[kind] = len(pool)
        chosen = None
        for c in sorted(pool, key=lambda c: h(f"{repo}/{kind}/{c['id']}")):
            if c["name"] in taken_names:
                skipped.append(
                    {"repo": repo, "kind": kind, "id": c["id"], "reason": "already used by another case"}
                )
                continue
            why = None
            if pool is s["defs"]:
                why = fn_filter(kind, c)
            elif kind in ("field_rename", "field_type"):
                n = field_mentions(c["name"])
                c["mentions"] = n
                if not 3 <= n <= CAP:
                    why = f"{n} uses (want 3 to {CAP})"
                elif kind == "field_type" and not INT_TYPES.match(c["type"]):
                    why = f"type {c['type']!r} is not a plain integer"
            elif kind == "global_rename":
                n = len(re.findall(r"(?<![\.\>\w])" + re.escape(c["name"]) + r"\b", big)) - 1
                c["mentions"] = n
                if not 2 <= n <= CAP:
                    why = f"{n} uses (want 2 to {CAP})"
            else:
                n = macro_uses(c["name"])
                c["mentions"] = n
                if not 2 <= n <= CAP:
                    why = f"{n} uses (want 2 to {CAP})"
            if why:
                skipped.append({"repo": repo, "kind": kind, "id": c["id"], "reason": why})
                continue
            chosen = c
            break
        if chosen is None:
            raise SystemExit(f"{repo}/{kind}: no candidate passed")
        taken_names.add(chosen["name"])
        diff, edit, reason = KINDS[kind]
        case = {
            "id": f"{repo}-{len(cases) + 1:02d}",
            "repo": repo,
            "kind": kind,
            "difficulty": diff,
            "symbol": chosen["id"],
            "edit": edit,
            "why_this_kind": reason,
        }
        for k in ("file", "line", "type", "params", "ret"):
            if k in chosen:
                case[k] = chosen[k]
        if "uses" in chosen:
            case["counts"] = chosen["uses"]
        elif "mentions" in chosen:
            case["counts"] = {"mentions": chosen["mentions"]}
        if repo == "curl":
            case["compiled"] = "to confirm after configure"
        cases.append(case)
    return cases, skipped, pools


def main() -> None:
    roots = {"redis": Path(sys.argv[1]), "sqlite": Path(sys.argv[2]), "curl": Path(sys.argv[3])}
    out = {
        "seed": SEED,
        "cap": CAP,
        "kinds": {k: {"difficulty": v[0], "edit": v[1], "why": v[2]} for k, v in KINDS.items()},
        "cases": [],
        "pools": {},
        "skipped": [],
    }
    for repo, root in roots.items():
        cases, skipped, pools = pick(repo, root)
        out["cases"] += cases
        out["skipped"] += skipped
        out["pools"][repo] = pools
    json.dump(out, sys.stdout, indent=1)
    print()


if __name__ == "__main__":
    main()
