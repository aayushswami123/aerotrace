"""Ask each compared tool "what will this change break?" for one case, the way it is normally used.

Every tool works on the unchanged, built tree <work>/<repo>/base, over the files of the build
(`bench/cases/make_patches.files_of`). Each answer is a list of (file, line) plus the text an agent would
read. AeroTrace is the only tool that reads the patch, because `aerotrace check` is how it is used.

Usage (one case, prints JSON): python3 -I bench/truth/tools.py <work-dir> <case-id> <tool>
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CASES = HERE.parent / "cases"
sys.path.insert(0, str(CASES))
sys.path.insert(0, str(HERE.parent / "accuracy"))
from cfuncs import blank_comments_and_strings  # noqa: E402
from make_patches import files_of  # noqa: E402

CLANGD = "/usr/bin/clangd"
REPO_ROOT = HERE.parents[1]


def name_of(case: dict) -> str:
    s = case["symbol"]
    return s.split(".", 1)[1] if case["kind"].startswith("field_") else s.rsplit(":", 1)[1]


def build_files(base: Path, repo: str) -> list[str]:
    return sorted(os.path.relpath(p, base) for p in files_of(base, repo))


def definition_position(base: Path, case: dict) -> tuple[str, int, int]:
    """(file, 0-based line, 0-based column) of the symbol's name where it is defined."""
    name = name_of(case)
    if case["kind"].startswith("field_"):
        rel = case["file"]
        blank = blank_comments_and_strings((base / rel).read_text(errors="replace"))
        struct = case["symbol"].split(".", 1)[0]
        m = re.search(r"^(?:typedef )?struct " + struct + r"\s*\{", blank, re.M)
        depth, i = 1, m.end()
        while depth:
            c = blank[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            elif (
                depth == 1
                and blank.startswith(name, i)
                and not re.match(r"\w", blank[i - 1])
                and re.match(r"\s*[;,\[:]", blank[i + len(name) :])
            ):
                break
            i += 1
        pos = i
    else:
        rel = case["symbol"].rsplit(":", 1)[0]
        lines = (base / rel).read_text(errors="replace").split("\n")
        ln = case["line"] - 1
        for k in range(ln, min(ln + 3, len(lines))):
            col = re.search(r"\b" + re.escape(name) + r"\b", lines[k])
            if col:
                return rel, k, col.start()
        raise ValueError(f"{name} not found near {rel}:{case['line']}")
    text = (base / rel).read_text(errors="replace")
    line = text.count("\n", 0, pos)
    col = pos - (text.rfind("\n", 0, pos) + 1)
    return rel, line, col


def timed(fn, runs: int = 3):
    times, out = [], None
    for _ in range(runs):
        t = time.perf_counter()
        out = fn()
        times.append(time.perf_counter() - t)
    return out, sorted(times)[len(times) // 2]


# ---------------------------------------------------------------- ripgrep


def ask_rg(base: Path, case: dict) -> dict:
    files = build_files(base, case["repo"])
    cmd = ["rg", "-n", "-w", "--no-heading", "--with-filename", name_of(case), *files]
    r, secs = timed(lambda: subprocess.run(cmd, cwd=base, capture_output=True, text=True, errors="replace"))
    hits = []
    for ln in r.stdout.splitlines():
        m = re.match(r"^(.+?):(\d+):", ln)
        if m:
            hits.append([m.group(1), int(m.group(2))])
    return {
        "command": "rg -n -w --no-heading --with-filename " + name_of(case) + " <build files>",
        "hits": hits,
        "text": r.stdout,
        "seconds": round(secs, 4),
    }


# ---------------------------------------------------------------- cscope


def cscope_setup(base: Path, repo: str) -> float:
    (base / "cscope.files").write_text("\n".join(build_files(base, repo)) + "\n")
    t = time.perf_counter()
    subprocess.run(
        ["cscope", "-b", "-q", "-k", "-i", "cscope.files"], cwd=base, check=True, capture_output=True
    )
    return time.perf_counter() - t


def ask_cscope(base: Path, case: dict) -> dict:
    cmd = ["cscope", "-d", "-L0", name_of(case)]
    r, secs = timed(lambda: subprocess.run(cmd, cwd=base, capture_output=True, text=True, errors="replace"))
    hits = []
    for ln in r.stdout.splitlines():
        parts = ln.split(" ", 3)
        if len(parts) >= 3 and parts[2].isdigit():
            hits.append([parts[0], int(parts[2])])
    return {
        "command": "cscope -d -L0 " + name_of(case),
        "hits": hits,
        "text": r.stdout,
        "seconds": round(secs, 4),
    }


# ---------------------------------------------------------------- clangd (a minimal LSP client)


class Clangd:
    def __init__(self, base: Path):
        self.base = base
        self.p = subprocess.Popen(
            [CLANGD, "--background-index", f"--compile-commands-dir={base}", "-j=8", "--log=error"],
            cwd=base, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )  # fmt: skip
        self.n = 0
        self.indexing: set[str] = set()
        self.index_done = False

    def send(self, msg: dict) -> None:
        body = json.dumps(msg).encode()
        self.p.stdin.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
        self.p.stdin.flush()

    def read(self) -> dict:
        length = 0
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("clangd exited")
            if line in (b"\r\n", b"\n"):
                break
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":")[1])
        return json.loads(self.p.stdout.read(length))

    def request(self, method: str, params: dict) -> dict:
        self.n += 1
        rid = self.n
        self.send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        while True:
            m = self.read()
            self._note(m)
            if m.get("id") == rid and "method" not in m:
                return m

    def _note(self, m: dict) -> None:
        if m.get("method") == "window/workDoneProgress/create":
            self.send({"jsonrpc": "2.0", "id": m["id"], "result": None})
        elif m.get("method") == "$/progress":
            tok, val = str(m["params"]["token"]), m["params"]["value"]
            if "index" in tok.lower():
                if val.get("kind") == "begin":
                    self.indexing.add(tok)
                elif val.get("kind") == "end":
                    self.indexing.discard(tok)
                    self.index_done = True
        elif "id" in m and "method" in m:  # any other request from the server: answer with null
            self.send({"jsonrpc": "2.0", "id": m["id"], "result": None})

    def start(self, first_file: str) -> float:
        t = time.perf_counter()
        self.request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": self.base.as_uri(),
                "capabilities": {"window": {"workDoneProgress": True}},
            },
        )
        self.send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
        self.open(first_file)
        while not self.index_done or self.indexing:  # wait for the background index to finish
            self._note(self.read())
        return time.perf_counter() - t

    def open(self, rel: str) -> None:
        p = self.base / rel
        self.send(
            {
                "jsonrpc": "2.0",
                "method": "textDocument/didOpen",
                "params": {
                    "textDocument": {
                        "uri": p.as_uri(),
                        "languageId": "c",
                        "version": 1,
                        "text": p.read_text(errors="replace"),
                    }
                },
            }
        )

    def references(self, rel: str, line: int, col: int) -> list[dict]:
        self.open(rel)
        r = self.request(
            "textDocument/references",
            {
                "textDocument": {"uri": (self.base / rel).as_uri()},
                "position": {"line": line, "character": col},
                "context": {"includeDeclaration": False},
            },
        )
        return r.get("result") or []

    def stop(self) -> None:
        try:
            self.request("shutdown", {})
            self.send({"jsonrpc": "2.0", "method": "exit"})
            self.p.wait(10)
        except Exception:
            self.p.kill()


def ask_clangd(server: Clangd, base: Path, case: dict) -> dict:
    rel, line, col = definition_position(base, case)
    refs, secs = timed(lambda: server.references(rel, line, col))
    hits, text = [], []
    base_real = os.path.realpath(base)
    for r in refs:
        path = os.path.realpath(r["uri"].removeprefix("file://"))
        f = os.path.relpath(path, base_real)
        ln = r["range"]["start"]["line"] + 1
        hits.append([f, ln])
        try:
            src = Path(path).read_text(errors="replace").split("\n")[ln - 1].strip()
        except (OSError, IndexError):
            src = ""
        text.append(f"{f}:{ln}: {src}")
    hits.sort()
    return {
        "command": f"textDocument/references at {rel}:{line + 1}:{col + 1}",
        "hits": hits,
        "text": "\n".join(sorted(text)) + "\n",
        "seconds": round(secs, 4),
    }


# ---------------------------------------------------------------- AeroTrace


def aerotrace(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["uv", "run", "--project", str(REPO_ROOT), "aerotrace", *args],
                          capture_output=True, text=True, errors="replace")  # fmt: skip


def aerotrace_setup(base: Path) -> float:
    t = time.perf_counter()
    r = aerotrace("index", "--root", str(base))
    if r.returncode:
        raise RuntimeError("aerotrace index failed: " + r.stdout + r.stderr)
    return time.perf_counter() - t


def ask_aerotrace(base: Path, case: dict) -> dict:
    patch = CASES / "patches" / f"{case['id']}.diff"
    r, secs = timed(lambda: aerotrace("check", "--patch", str(patch), "--json", "--root", str(base)))
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"error": "no JSON: " + r.stdout[-2000:] + r.stderr[-2000:], "seconds": round(secs, 4)}
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from aerotrace import ui

    text = ui.render_text(data, color=False, unicode=False, limit=15)  # what the MCP `check` tool returns
    claimed, anywhere = [], []
    for n in data.get("nodes", []):
        if n["status"] in ("changed", "new", "removed", "no_effect"):
            continue  # the change itself, or "checked, no effect found": not part of what it says may break
        lines = n.get("lines") or [n["line"]]
        for ln in lines:
            anywhere.append([n["file"], ln])
            if n["status"] == "breaks":
                claimed.append([n["file"], ln])
        if not n.get("lines"):
            continue
    return {
        "command": "aerotrace check --patch <case.diff> --json",
        "hits": claimed,
        "hits_anywhere": anywhere,
        "nodes": [
            {k: n.get(k) for k in ("id", "status", "certainty", "file", "line", "lines", "distance")}
            for n in data.get("nodes", [])
        ],
        "omitted": data.get("omitted"),
        "summary": data.get("summary"),
        "text": text,
        "exit": r.returncode,
        "seconds": round(secs, 4),
    }
