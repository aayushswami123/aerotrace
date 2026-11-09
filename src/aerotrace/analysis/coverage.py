"""Recorded test coverage: which recorded test ran which function. Stored in `.aerotrace/tests.json`.

This is a separate file, not part of the index (the index format does not change). `aerotrace tests
record` writes it; `aerotrace tests` reads it. Everything here is pure data work: parsing gcov output,
matching it to indexed functions, and checking whether the data still fits the code. Running tests and
gcov lives in `toolchain/testrun.py`.

How sure coverage is:
- A recorded test that ran a function is a strong link, but only while the file is the same as when it was
  recorded (checked by content hash).
- "None of the recorded runs executed it" is never `exact`: a process killed by a signal, `_exit()`,
  `abort()`, a crash or a timeout writes no counts, and flaky paths vary from run to run.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

FORMAT = "aerotrace-tests"
VERSION = 1
FILE_NAME = "tests.json"
PARTIAL_NAME = "tests.partial.json"

_CLONE = re.compile(r"(\.(constprop|isra|part|cold|lto_priv|localalias|clone)(\.\d+)?)+$")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str | None:
    try:
        return sha256_bytes(path.read_bytes())
    except OSError:
        return None


# ---------------------------------------------------------------- gcov output


@dataclass(frozen=True)
class GcovFn:
    source: str  # the source path as gcov reports it (may be relative to the compile folder)
    name: str  # demangled when gcov gives it, clone suffixes removed
    line: int  # the line gcov gives for the function (its name line)
    end_line: int  # 0 when gcov does not say
    count: int


def clean_name(name: str) -> str:
    """`foo.constprop.0` -> `foo`; `ns::A::f(int) const` -> `ns::A::f`."""
    name = _CLONE.sub("", name.strip())
    if "(" in name:
        depth = 0
        for i, ch in enumerate(name):  # cut at the first '(' outside template brackets
            if ch == "<":
                depth += 1
            elif ch == ">":
                depth -= 1
            elif ch == "(" and depth <= 0:
                name = name[:i]
                break
    return name.strip()


def parse_llvm_intermediate(text: str) -> list[GcovFn]:
    """LLVM gcov `-i` output: `file:<path>` then `function:<line>,<count>,<name>` lines."""
    out: list[GcovFn] = []
    source = ""
    for line in text.splitlines():
        if line.startswith("file:"):
            source = line[5:]
        elif line.startswith("function:") and source:
            parts = line[9:].split(",", 2)
            if len(parts) == 3 and parts[0].isdigit() and parts[1].lstrip("-").isdigit():
                out.append(GcovFn(source, clean_name(parts[2]), int(parts[0]), 0, int(parts[1])))
    return out


def parse_gnu_json(text: str) -> list[GcovFn]:
    """GNU gcov `--json-format` output (one JSON document per line, or one document)."""
    out: list[GcovFn] = []
    docs = []
    text = text.strip()
    if not text:
        return out
    try:
        docs = [json.loads(text)]
    except json.JSONDecodeError:
        docs = [json.loads(ln) for ln in text.splitlines() if ln.strip().startswith("{")]
    for doc in docs:
        for f in doc.get("files", []):
            src = f.get("file", "")
            for fn in f.get("functions", []):
                name = fn.get("demangled_name") or fn.get("name") or ""
                out.append(
                    GcovFn(
                        src,
                        clean_name(name),
                        int(fn.get("start_line", 0)),
                        int(fn.get("end_line", 0)),
                        int(fn.get("execution_count", 0)),
                    )
                )
    return out


# ---------------------------------------------------------------- matching gcov functions to the index


@dataclass
class IndexFile:
    path: str
    functions: list[tuple[str, int, int]]  # (qualified name, line_start, line_end), sorted by line


def index_files(conn: sqlite3.Connection) -> dict[str, IndexFile]:
    out: dict[str, IndexFile] = {}
    for r in conn.execute(
        "SELECT qualified_name, file, line_start, line_end FROM functions ORDER BY file, line_start"
    ):
        out.setdefault(r[1], IndexFile(r[1], [])).functions.append((r[0], r[2], r[3]))
    return out


def resolve_source(source: str, dirs: list[Path], root: Path, by_hash: dict[str, str]) -> str | None:
    """The repo-relative path of a source gcov reports, or None when it is outside the repo and its
    content matches no indexed file (a generated file such as SQLite's amalgamation matches by hash)."""
    cands = [Path(source)] if os.path.isabs(source) else [d / source for d in dirs] + [root / source]
    for c in cands:
        if not c.is_file():
            continue
        real = Path(os.path.realpath(c))
        try:
            return real.relative_to(Path(os.path.realpath(root))).as_posix()
        except ValueError:
            h = sha256_file(real)
            return by_hash.get(h or "")
    return None


def match(fns: list[IndexFile], name: str, line: int) -> str | None:
    """The indexed function a gcov function belongs to: the innermost function whose lines contain
    `line` (lambdas and blocks fold into the function around them). Returns its qualified name."""
    best: tuple[int, str] | None = None
    for f in fns:
        for q, a, b in f.functions:
            # gcov's line is the name line; libclang's extent may start a line or two earlier.
            if a <= line <= b:
                span = b - a
                if best is None or span < best[0] or (span == best[0] and q.split("::")[-1] == name):
                    best = (span, q)
    return best[1] if best else None


# ---------------------------------------------------------------- tests.json


@dataclass
class FileCoverage:
    sha256: str
    functions: dict[str, set[str]] = field(default_factory=dict)  # qualified name -> test ids that ran it
    lines: dict[str, int] = field(default_factory=dict)  # qualified name -> line_start when recorded
    notes_older: bool = False  # gcov said the source is newer than its .gcno: built from other code


@dataclass
class TestRun:
    id: str
    exit: str  # "0", "1", ..., "timeout", "not run", "signal 9"


@dataclass
class Coverage:
    complete: bool
    commit: str | None
    config_sha256: str
    config: dict
    gcov: str
    run: str  # the run template, for ready-to-run commands
    run_many: str
    each: str
    tests: list[TestRun] = field(default_factory=list)
    files: dict[str, FileCoverage] = field(default_factory=dict)
    inputs: dict[str, str] = field(default_factory=dict)  # test input files (repo-relative) -> sha256
    dropped: dict[str, int] = field(default_factory=dict)  # why some gcov functions were not kept
    aerotrace: str = ""

    def to_json(self) -> str:
        ids = sorted(t.id for t in self.tests)
        pos = {t: i for i, t in enumerate(ids)}
        by_id = {t.id: t for t in self.tests}
        doc = {
            "format": FORMAT,
            "version": VERSION,
            "aerotrace": self.aerotrace,
            "complete": self.complete,
            "recorded": {
                "commit": self.commit,
                "config_sha256": self.config_sha256,
                "config": self.config,
                "gcov": self.gcov,
            },
            "run": {"run": self.run, "run_many": self.run_many, "each": self.each},
            "tests": [{"id": t, "exit": by_id[t].exit} for t in ids],
            "inputs": dict(sorted(self.inputs.items())),
            "files": {
                p: {
                    "sha256": fc.sha256,
                    "notes_older": fc.notes_older,
                    "functions": [
                        [q, fc.lines.get(q, 0), sorted(pos[t] for t in fc.functions[q] if t in pos)]
                        for q in sorted(fc.functions, key=lambda q: (fc.lines.get(q, 0), q))
                    ],
                }
                for p, fc in sorted(self.files.items())
            },
            "dropped": dict(sorted(self.dropped.items())),
        }
        return json.dumps(doc, separators=(",", ":")) + "\n"


class CoverageError(ValueError):
    pass


def from_json(text: str) -> Coverage:
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as e:
        raise CoverageError(f"it is not valid JSON ({e.msg})") from e
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise CoverageError("it is not an AeroTrace test file")
    if doc.get("version") != VERSION:
        raise CoverageError(f"it has format version {doc.get('version')}, this AeroTrace reads {VERSION}")
    try:
        rec = doc["recorded"]
        tests = [TestRun(t["id"], str(t["exit"])) for t in doc["tests"]]
        ids = [t.id for t in tests]
        files = {}
        for p, f in doc["files"].items():
            fc = FileCoverage(f["sha256"], notes_older=bool(f.get("notes_older")))
            for q, line, idx in f["functions"]:
                fc.functions[q] = {ids[i] for i in idx}
                fc.lines[q] = int(line)
            files[p] = fc
        run = doc.get("run", {})
        return Coverage(
            bool(doc["complete"]),
            rec.get("commit"),
            rec.get("config_sha256", ""),
            rec.get("config", {}),
            rec.get("gcov", ""),
            run.get("run", ""),
            run.get("run_many", ""),
            run.get("each", ""),
            tests,
            files,
            dict(doc.get("inputs", {})),
            dict(doc.get("dropped", {})),
            doc.get("aerotrace", ""),
        )
    except (KeyError, TypeError, ValueError, IndexError) as e:
        raise CoverageError(f"it is damaged ({e.__class__.__name__}: {e})") from e


def path(root: Path, partial: bool = False) -> Path:
    from aerotrace.index.store import index_dir

    return index_dir(root) / (PARTIAL_NAME if partial else FILE_NAME)


def load(root: Path) -> tuple[Coverage | None, str]:
    """(coverage, note). Reads tests.json; a partial file is used only when no complete one exists."""
    for partial in (False, True):
        p = path(root, partial)
        if not p.is_file():
            continue
        try:
            return from_json(p.read_text(encoding="utf-8")), ""
        except (OSError, CoverageError) as e:
            return (
                None,
                f"Test data in .aerotrace/{p.name} was not used: {e}. Run `aerotrace tests record` again.",
            )
    return None, ""


def write(root: Path, cov: Coverage, partial: bool = False) -> Path:
    from aerotrace.index.store import ensure_index_dir

    ensure_index_dir(root)
    p = path(root, partial)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(cov.to_json(), encoding="utf-8")
    tmp.replace(p)
    return p


def changed_inputs(root: Path, cov: Coverage) -> list[str]:
    """Test input files (from the config's `inputs`) that changed, appeared or vanished since recording."""
    out = []
    for rel, h in sorted(cov.inputs.items()):
        if sha256_file(root / rel) != h:
            out.append(rel)
    globs = cov.config.get("inputs", []) or []
    now = expand_inputs(root, globs)
    out += sorted(set(now) - set(cov.inputs))
    return sorted(set(out))


def expand_inputs(root: Path, globs: list[str]) -> list[str]:
    found: set[str] = set()
    for g in globs:
        for p in root.glob(g):
            if p.is_file():
                found.add(p.relative_to(root).as_posix())
    return sorted(found)


def file_state(root: Path, cov: Coverage, rel: str, base_text: bytes | None) -> str:
    """fresh | outdated | missing, for one source file. Fresh when the recorded content equals the file
    on disk or its version at the base of the change."""
    fc = cov.files.get(rel)
    if fc is None:
        return "missing"
    if fc.notes_older:
        return "outdated"
    if sha256_file(root / rel) == fc.sha256:
        return "fresh"
    if base_text is not None and sha256_bytes(base_text) == fc.sha256:
        return "fresh"
    return "outdated"


def find_fn(fc: FileCoverage, qname: str, line: int) -> str | None:
    """The recorded key for a function: same qualified name (nearest recorded line when overloaded)."""
    if qname in fc.functions:
        return qname
    return None


def failed(cov: Coverage) -> set[str]:
    return {t.id for t in cov.tests if t.exit != "0"}
