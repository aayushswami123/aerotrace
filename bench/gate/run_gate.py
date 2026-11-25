#!/usr/bin/env python3
"""The gate: run AeroTrace over saved cases and compare its answer with what the compiler says.

For every case in ``cases/<id>/`` (see README.md) this script:
  1. copies ``base/`` into a fresh temp folder, writes compile_commands.json, makes a git commit,
     builds the baseline with ``cc`` (a baseline that does not build is an ERROR), runs ``index``;
  2. patch mode: ``check --patch change.patch`` (exit code, first line, ``--json`` nodes);
  3. applies the patch and runs ``check`` (git mode);
  4. builds the patched tree with ``cc``/``c++`` (the truth: builds or fails, and where);
  5. puts each mode in one bucket (see README.md).

Standard library only. Nothing is written into the repo except the optional ``--markdown`` file.
Exit code: 1 if anything is worse than baseline.json (see README.md), else 0.
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from compare import (
    MODES,
    Verdict,
    class_of,
    compare,
    expectation_error,
    known_block,
    render_markdown,
    render_text,
    summary_lines,
)

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
CASES = HERE / "cases"
BASELINE = HERE / "baseline.json"
KNOWN = HERE / "known_limits.toml"

SOURCE_SUFFIXES = (".c", ".cpp", ".S")
GIT = ["git", "-c", "user.email=gate@example.invalid", "-c", "user.name=gate", "-c", "commit.gpgsign=false"]
TIMEOUT = 300


@dataclass
class Case:
    id: str
    path: Path
    summary: str
    origin: str
    expect_build: str  # "fails" | "passes" | "n/a"
    failing_sites: list[str] = field(default_factory=list)
    build_flags: list[str] = field(default_factory=list)
    compdb_flags: list[str] = field(default_factory=list)
    not_in_build: list[str] = field(default_factory=list)
    modes: list[str] = field(default_factory=lambda: list(MODES))
    patch_applies: bool = True
    needs: str = ""


@dataclass
class Build:
    ok: bool
    files: list[str] = field(default_factory=list)  # files with compile errors
    symbols: list[str] = field(default_factory=list)  # symbols the linker complained about
    text: str = ""  # all error text, for matching only


@dataclass
class Answer:
    rc: int
    first: str
    breaks: list[dict] = field(default_factory=list)  # json nodes with status "breaks"
    bucket: str = ""


@dataclass
class Result:
    case: Case
    error: str = ""
    truth: Build | None = None
    answers: dict[str, Answer] = field(default_factory=dict)


def load_cases(only: str | None) -> list[Case]:
    cases = []
    for d in sorted(p for p in CASES.iterdir() if p.is_dir()):
        raw = tomllib.loads((d / "case.toml").read_text())
        cases.append(Case(path=d, **raw))
    if only:
        cases = [c for c in cases if c.id == only]
        if not cases:
            sys.exit(f"no case with id {only!r}")
    return cases


def sh(cmd: list[str], cwd: Path, check: bool = False) -> subprocess.CompletedProcess[str]:
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=TIMEOUT)
    if check and p.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:3])} failed: {(p.stderr or p.stdout).strip()[:200]}")
    return p


def sources(w: Path) -> list[str]:
    return sorted(f.name for f in w.iterdir() if f.is_file() and f.suffix in SOURCE_SUFFIXES)


def compiler(name: str) -> list[str]:
    return ["c++", "-std=c++17"] if name.endswith(".cpp") else ["cc"]


def write_compdb(case: Case, w: Path) -> None:
    entries = [
        {"directory": str(w), "file": f, "arguments": [*compiler(f), *case.compdb_flags, "-c", f]}
        for f in sources(w)
        if f.endswith((".c", ".cpp")) and f not in case.not_in_build
    ]
    (w / "compile_commands.json").write_text(json.dumps(entries))


ERR_RE = re.compile(r"^([^\s:][^:]*):\d+:\d+: (?:fatal )?error:", re.M)
SYM_RES = (
    re.compile(r'^\s*"([^"]+)", referenced from', re.M),  # macOS undefined
    re.compile(r"duplicate symbol '([^']+)'", re.M),  # macOS duplicate
    re.compile(r"undefined reference to [`']([^'`]+)'", re.M),  # GNU undefined
    re.compile(r"multiple definition of [`']([^'`]+)'", re.M),  # GNU duplicate
)


def build(case: Case, w: Path) -> Build:
    """Compile every source file (all error messages), then link. Never uses the user's build."""
    out = Path(tempfile.mkdtemp(prefix="gate-obj-"))
    try:
        text, files, objs = "", set(), []
        for f in sources(w):
            o = out / (f + ".o")
            p = sh([*compiler(f), *case.build_flags, "-ferror-limit=0", "-c", f, "-o", str(o)], w)
            text += p.stderr
            if p.returncode != 0:
                files.update({Path(m).name for m in ERR_RE.findall(p.stderr)} or {f})
            else:
                objs.append(str(o))
        if files:
            return Build(False, sorted(files), [], text)
        p = sh(["c++", *objs, "-o", str(out / "a.out")], w)
        if p.returncode == 0:
            return Build(True)
        syms = sorted({_clean(s) for r in SYM_RES for s in r.findall(p.stderr)})
        return Build(False, [], syms, p.stderr)
    finally:
        shutil.rmtree(out, ignore_errors=True)


def _clean(sym: str) -> str:
    sym = sym.removeprefix("_") if not sym.startswith("__") else sym
    return re.sub(r"\(.*", "", sym)


def first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip() and not line.startswith("(Re-indexed"):
            return line.strip()
    return ""


def ask(at: list[str], w: Path, args: list[str]) -> Answer:
    p = sh([*at, "check", *args], w)
    ans = Answer(p.returncode, first_line(p.stdout + p.stderr))
    j = sh([*at, "check", "--json", *args], w)
    try:
        doc = json.loads(j.stdout)
        ans.breaks = [n for n in doc.get("nodes", []) if n.get("status") == "breaks"]
    except (ValueError, AttributeError):
        ans.breaks = []
    return ans


def classify(case: Case, truth: Build, ans: Answer) -> str:
    if case.expect_build == "n/a":  # no build truth: only "exit 0" is wrong
        return "false safe" if ans.rc == 0 else "correct"
    if truth.ok:
        return {0: "correct", 1: "false alarm", 2: "false unknown"}.get(ans.rc, "other")
    if ans.rc == 0:
        return "false safe"
    if ans.rc == 2:
        return "honest unknown"
    if ans.rc == 1:
        return "correct" if _names_failure(ans, truth) else "weak"
    return "other"


def _names_failure(ans: Answer, truth: Build) -> bool:
    """True if a BREAKS / MAY BREAK node is in a failing file or is a failing symbol.

    "weak" is only reported when the JSON names files and none of them match. If the JSON has no
    BREAKS nodes at all we cannot tell, so we do not guess: the answer counts as correct.
    """
    if not ans.breaks:
        return True
    for n in ans.breaks:
        name = str(n.get("name") or "").split("::")[-1]
        if n.get("file") in truth.files or name in truth.symbols:
            return True
        if name and re.search(rf"\b{re.escape(name)}\b", truth.text):
            return True
    return False


def supported(needs: str) -> bool:
    return "arm64" not in needs or platform.machine() == "arm64"


def preflight() -> str:
    """One plain line if this machine cannot run the gate, else an empty string."""
    if sys.platform != "darwin":
        return "The gate runs on macOS with Apple clang only for now."
    if "clang" not in sh(["cc", "--version"], HERE).stdout.lower():
        return "The gate needs clang as `cc` (macOS with Apple clang only for now)."
    return ""


def run_case(case: Case, at: list[str]) -> Result:
    res = Result(case)
    if case.needs and not supported(case.needs):
        res.error = f"skipped: needs {case.needs} (the gate is not green without it)"
        return res
    tmp = Path(tempfile.mkdtemp(prefix="gate-"))
    w = tmp / "w"
    try:
        shutil.copytree(case.path / "base", w)
        write_compdb(case, w)
        sh(["git", "init", "-q", "."], w, check=True)
        sh(["git", "add", "-A"], w, check=True)
        sh([*GIT, "commit", "-qm", "base"], w, check=True)
        base = build(case, w)
        if not base.ok:
            res.error = f"baseline does not build ({', '.join(base.files + base.symbols) or 'link'})"
            return res
        sh([*at, "index"], w)
        patch = str(case.path / "change.patch")
        if "patch" in case.modes:
            res.answers["patch"] = ask(at, w, ["--patch", patch])
        if case.patch_applies:
            p = sh(["git", "apply", patch], w)
            if p.returncode != 0:
                res.error = f"patch does not apply: {p.stderr.strip()[:120]}"
                return res
            res.truth = build(case, w)
            res.error = expectation_error(
                case.id,
                case.expect_build,
                res.truth.ok,
                case.failing_sites,
                res.truth.files + res.truth.symbols,
            )
            if res.error:
                return res
            if "git" in case.modes:
                res.answers["git"] = ask(at, w, [])
        else:
            res.truth = Build(False)
        for ans in res.answers.values():
            ans.bucket = classify(case, res.truth, ans)
    except (RuntimeError, subprocess.TimeoutExpired, OSError) as e:
        res.error = str(e)[:200]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return res


def truth_text(r: Result) -> str:
    t = r.truth
    if t is None:
        return "ERROR"
    if r.case.expect_build == "n/a":
        return "n/a"
    return "passes" if t.ok else "fails"


def sites(r: Result) -> str:
    if not r.truth or r.truth.ok:
        return ""
    return ", ".join((r.truth.files + r.truth.symbols)[:3])


def to_data(results: list[Result], before: dict[str, Result] | None, measured: str) -> dict:
    """Turn run results into the plain data that is compared and saved (see compare.py)."""
    cases: dict[str, dict] = {}
    for r in results:
        e: dict = {"build": truth_text(r), "expect": r.case.expect_build, "sites": sites(r), "error": r.error}
        old = before.get(r.case.id) if before else None
        for mode, a in sorted(r.answers.items()):
            e[mode] = {"rc": a.rc, "class": a.bucket}
            oa = old.answers.get(mode) if old else None
            if oa:
                e[mode]["before"] = f"{oa.rc} {oa.bucket}"
        cases[r.case.id] = e
    return {"measured": measured, "cases": cases}


def load_known() -> tuple[dict[tuple[str, str], str], str]:
    raw = tomllib.loads(KNOWN.read_text())
    known = {(x["id"], m): x["why"] for x in raw["limit"] for m in x["modes"]}
    return known, raw["issue"]


def load_baseline() -> dict | None:
    return json.loads(BASELINE.read_text()) if BASELINE.exists() else None


def carry_before(data: dict, old: dict | None) -> None:
    """Keep the 'before' cells of the saved baseline when this run did not measure an older build."""
    if not old:
        return
    for i, e in data["cases"].items():
        for mode in MODES:
            prev = old["cases"].get(i, {}).get(mode, {}).get("before")
            if prev and mode in e and "before" not in e[mode]:
                e[mode]["before"] = prev


def measured_label(args: argparse.Namespace) -> str:
    """The last commit that changed the AeroTrace code (src/), so the label ignores bench-only commits."""
    if args.measured:
        return args.measured
    if args.at:
        return "custom build (--at)"
    sha = sh(["git", "log", "-1", "--format=%h", "--", "src", "pyproject.toml"], REPO).stdout.strip()
    dirty = sh(["git", "status", "--porcelain", "--", "src", "pyproject.toml"], REPO).stdout.strip()
    return sha + (" (with uncommitted changes in src/)" if dirty else "")


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the gate cases against an AeroTrace build.")
    ap.add_argument("--at", help="aerotrace command to test (default: uv run aerotrace from this repo)")
    ap.add_argument("--before", help="a second, older build: adds its exit codes as 'before' columns")
    ap.add_argument("--only", help="run one case id (no comparison with the baseline)")
    ap.add_argument(
        "--write-baseline", action="store_true", help="save baseline.json and BASELINE.md (on purpose)"
    )
    ap.add_argument("--markdown", help="also write the report to this file")
    ap.add_argument("--measured", help="text for the report header: which AeroTrace code was measured")
    ap.add_argument("--jobs", type=int, default=4, help="cases to run at the same time (default 4)")
    args = ap.parse_args()
    if problem := preflight():
        print(problem)
        return 1

    at = shlex.split(args.at) if args.at else ["uv", "run", "--quiet", "--project", str(REPO), "aerotrace"]
    cases = load_cases(args.only)
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        results = sorted(pool.map(lambda c: run_case(c, at), cases), key=lambda r: r.case.id)
        before = None
        if args.before:
            old = shlex.split(args.before)
            before = {r.case.id: r for r in pool.map(lambda c: run_case(c, old), cases)}

    data = to_data(results, before, measured_label(args))
    known, issue = load_known()
    saved = load_baseline()
    partial = bool(args.only)
    compare_to = None if partial or args.write_baseline else saved
    if args.write_baseline:
        carry_before(data, saved)
    with_before = bool(before)

    print("\n".join(render_text(data["cases"], with_before)) + "\n")
    print("\n".join(summary_lines(data, compare_to, with_before)))
    k_lines, k_better = known_block(data, known, issue)
    print("\n".join(k_lines) + "\n")

    if args.write_baseline:
        if any(r.error for r in results):
            print(
                "Not writing the baseline: "
                + "; ".join(f"{r.case.id}: {r.error}" for r in results if r.error)
            )
            return 1
        BASELINE.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
        (HERE / "BASELINE.md").write_text(render_markdown(data))
        print("Wrote bench/gate/baseline.json and bench/gate/BASELINE.md.")
        return 0
    if args.markdown:
        Path(args.markdown).write_text(render_markdown(data))
    if saved is None and not partial:
        print("GATE FAILED: no baseline.json. Run `make gate-baseline` on purpose.")
        return 1
    if partial:
        v = Verdict()
        for i, e in data["cases"].items():
            for mode in MODES:
                if class_of(data["cases"], i, mode) == "false safe" and (i, mode) not in known:
                    v.failures.append(f"{i} ({mode} mode): false safe, and not in the known limits")
            if e["error"]:
                v.failures.append(f"{i}: ERROR: {e['error']}")
        print("(one case: not compared with the baseline)")
    else:
        v = compare(saved, data, known)
    v.better += k_better
    if v.changes:
        print("Cases that changed class (from -> to):")
        print("\n".join(f"  {c}" for c in v.changes) + "\n")
    if v.failures:
        print("GATE FAILED:")
        print("\n".join(f"  {f}" for f in v.failures))
        return 1
    if v.better:
        print("Better than the baseline: run `make gate-baseline` to record it.")
        print("\n".join(f"  {b}" for b in v.better))
    print("GATE PASSED: nothing is worse than the baseline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
