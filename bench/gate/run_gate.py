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
Exit code: 1 if any case is false safe, 2 if there is no false safe but a case had an ERROR, else 0.
"""

from __future__ import annotations

import argparse
import json
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

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
CASES = HERE / "cases"

BUCKETS = ("correct", "honest unknown", "false safe", "weak", "false alarm")
MODES = ("patch", "git")
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


def run_case(case: Case, at: list[str]) -> Result:
    res = Result(case)
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


def tally(results: list[Result], mode: str) -> dict[str, int]:
    t = dict.fromkeys(BUCKETS, 0) | {"false unknown": 0, "other": 0, "cases": 0}
    for r in results:
        a = r.answers.get(mode)
        if not a:
            continue
        t["cases"] += 1
        if a.bucket == "false unknown":
            t["false unknown"] += 1
            t["false alarm"] += 1
        else:
            t[a.bucket] += 1
    return t


def summary_lines(results: list[Result], mode: str) -> list[str]:
    t = tally(results, mode)
    lines = [
        f"{mode} mode ({t['cases']} cases)",
        f"  correct:        {t['correct']}",
        f"  honest unknown: {t['honest unknown']}",
        f"  false safe:     {t['false safe']}",
        f"  weak:           {t['weak']}",
        f"  false alarm:    {t['false alarm']} (of which false unknown: {t['false unknown']})",
    ]
    if t["other"]:
        lines.append(f"  other exit code: {t['other']}")
    return lines


def exit0_on_failing(results: list[Result], mode: str) -> int:
    return sum(
        1
        for r in results
        if r.truth
        and not r.truth.ok
        and r.case.expect_build == "fails"
        and mode in r.answers
        and r.answers[mode].rc == 0
    )


def cell(r: Result | None, mode: str) -> str:
    if r is None:
        return ""
    if r.error:
        return "ERROR"
    a = r.answers.get(mode)
    return f"{a.rc} {a.bucket}" if a else "-"


def report(results: list[Result], before: dict[str, Result] | None, markdown: bool) -> str:
    out: list[str] = []
    head = ["id", "build", "patch", "git", "failing site"]
    if before:
        head = ["id", "build", "patch before", "patch", "git before", "git", "failing site"]
    rows = []
    notes = []
    for r in results:
        b = before.get(r.case.id) if before else None
        build_cell = truth_text(r)
        if r.truth and r.case.expect_build in ("fails", "passes") and build_cell != r.case.expect_build:
            build_cell += " (expected " + r.case.expect_build + ")"
            notes.append(f"{r.case.id}: build result differs from case.toml")
        if r.error:
            notes.append(f"{r.case.id}: {r.error}")
        if before:
            rows.append(
                [
                    r.case.id,
                    build_cell,
                    cell(b, "patch"),
                    cell(r, "patch"),
                    cell(b, "git"),
                    cell(r, "git"),
                    sites(r),
                ]
            )
        else:
            rows.append([r.case.id, build_cell, cell(r, "patch"), cell(r, "git"), sites(r)])
    if markdown:
        out.append("| " + " | ".join(head) + " |")
        out.append("|" + "---|" * len(head))
        out += ["| " + " | ".join(row) + " |" for row in rows]
    else:
        widths = [max(len(x[i]) for x in [head, *rows]) for i in range(len(head))]
        for row in [head, *rows]:
            out.append("  ".join(c.ljust(widths[i]) for i, c in enumerate(row)).rstrip())
    out.append("")
    block: list[str] = []
    for mode in MODES:
        block += summary_lines(results, mode)
        block.append(f"  failing-build cases that exit 0: {exit0_on_failing(results, mode)}")
        if before:
            bres = list(before.values())
            block.append(
                f"  before (older build): failing-build cases that exit 0: {exit0_on_failing(bres, mode)}"
            )
            block += ["  before (older build):"] + ["  " + x for x in summary_lines(bres, mode)[1:]]
        block.append("")
    fs = [r.case.id for r in results for a in r.answers.values() if a.bucket == "false safe"]
    block.append("false safe ids: " + (", ".join(sorted(set(fs))) if fs else "none"))
    if notes:
        block += ["", "notes:"] + [f"  {n}" for n in notes]
    if markdown:
        out += ["", "```text", *block, "```"]
    else:
        out += block
    return "\n".join(out) + "\n"


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
    ap.add_argument("--only", help="run one case id")
    ap.add_argument("--markdown", help="also write the report to this file")
    ap.add_argument("--measured", help="text for the report header: which AeroTrace code was measured")
    ap.add_argument("--jobs", type=int, default=4, help="cases to run at the same time (default 4)")
    args = ap.parse_args()

    at = shlex.split(args.at) if args.at else ["uv", "run", "--quiet", "--project", str(REPO), "aerotrace"]
    cases = load_cases(args.only)
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        results = sorted(pool.map(lambda c: run_case(c, at), cases), key=lambda r: r.case.id)
        before = None
        if args.before:
            old = shlex.split(args.before)
            before = {r.case.id: r for r in pool.map(lambda c: run_case(c, old), cases)}

    print(report(results, before, markdown=False), end="")
    if args.markdown:
        header = (
            "# Gate baseline\n\n"
            f"AeroTrace code measured: `{measured_label(args)}`. Cases: {len(results)}. "
            "How to read this: see `README.md`.\n\n"
        )
        Path(args.markdown).write_text(header + report(results, before, markdown=True))
    if any(a.bucket == "false safe" for r in results for a in r.answers.values()):
        return 1
    return 2 if any(r.error for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
