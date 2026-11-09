"""`aerotrace tests record`: run the project's tests once, when the user asks, and record coverage.

This is the only place in AeroTrace that starts the user's commands, and only these rules make it safe:

- Nothing is guessed. The commands come from a config (`.aerotrace/tests.toml` or `--config`), which may
  come from a clone, so it is untrusted: the plan shows the config's path and SHA-256 and every command,
  and the user must say yes (or pass `--yes`; the plan is still printed).
- Each test runs with its own `GCOV_PREFIX` folder outside the repo, so its counters start at zero, no
  `.gcda` file lands in the source tree, and tests can run side by side. Any `GCOV_PREFIX` or
  `GCOV_PREFIX_STRIP` already set in the environment is removed (and that is said).
- Only `.aerotrace/tests.json` (or `tests.partial.json`) is written in the repo.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from aerotrace.analysis import coverage as cov

CONFIG_KEYS = {
    "version",
    "build",
    "objects",
    "tests",
    "list",
    "run",
    "run_many",
    "each",
    "timeout",
    "inputs",
    "gcov",
}


class ConfigError(ValueError):
    pass


@dataclass
class Config:
    path: Path
    sha256: str
    raw: dict
    build: str = ""
    objects: list[str] = field(default_factory=lambda: ["."])
    tests: list[str] = field(default_factory=list)
    list_cmd: str = ""
    run: str = ""
    run_many: str = ""
    each: str = ""
    timeout: int = 1800
    inputs: list[str] = field(default_factory=list)
    gcov: str = ""


EXAMPLE = """\
# .aerotrace/tests.toml: how `aerotrace tests record` runs your tests. Nothing runs without it.
version = 1
# build = "make -C src gcov"          # optional: run first (build with --coverage)
objects = ["src"]                      # folders holding the .gcno files
tests = ["unit/expire", "unit/type/string"]   # or: list = "<command printing one test id per line>"
run = "./runtest --single {test}"      # one test; {test} is quoted for the shell; {job} = parallel slot
run_many = "./runtest {tests}"         # optional: several tests in one command (for the answer)
each = "--single {test}"               # how one test is written inside {tests}
inputs = ["tests/**/*.tcl"]            # optional: test files to hash, so edits to them are noticed
timeout = 1800                         # seconds per test
"""


def default_config_path(root: Path) -> Path:
    from aerotrace.index.store import index_dir

    return index_dir(root) / "tests.toml"


def load_config(path: Path) -> Config:
    try:
        data = path.read_bytes()
    except OSError as e:
        raise ConfigError(f"could not read {path}: {e.strerror}") from e
    try:
        raw = tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"{path} is not valid TOML: {e}") from e
    unknown = sorted(set(raw) - CONFIG_KEYS)
    if unknown:
        raise ConfigError(f"{path}: unknown setting(s) {', '.join(unknown)}")
    if raw.get("version") != 1:
        raise ConfigError(f"{path}: needs `version = 1`")
    c = Config(path, cov.sha256_bytes(data), raw)

    def text(key: str) -> str:
        v = raw.get(key, "")
        if not isinstance(v, str):
            raise ConfigError(f"{path}: `{key}` must be a string")
        return v

    def strings(key: str, default: list[str]) -> list[str]:
        v = raw.get(key, default)
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            raise ConfigError(f"{path}: `{key}` must be a list of strings")
        return v

    c.build, c.list_cmd, c.run, c.run_many, c.each, c.gcov = (
        text(k) for k in ("build", "list", "run", "run_many", "each", "gcov")
    )
    c.objects = strings("objects", ["."])
    c.tests = strings("tests", [])
    c.inputs = strings("inputs", [])
    t = raw.get("timeout", 1800)
    if not isinstance(t, int) or t <= 0:
        raise ConfigError(f"{path}: `timeout` must be a positive number of seconds")
    c.timeout = t
    if "{test}" not in c.run:
        raise ConfigError(f"{path}: `run` must contain {{test}}")
    if bool(c.tests) == bool(c.list_cmd):
        raise ConfigError(f"{path}: give either `tests` (a list) or `list` (a command), not both or neither")
    if c.run_many and ("{tests}" not in c.run_many or "{test}" not in c.each):
        raise ConfigError(f"{path}: `run_many` needs {{tests}} and `each` needs {{test}}")
    return c


def command_for(template: str, test: str, job: int = 0) -> str:
    return template.replace("{test}", shlex.quote(test)).replace("{job}", str(job))


def ready_command(run: str, run_many: str, each: str, tests: list[str]) -> list[str]:
    """Commands that run these tests: one, when the config says how to join them, else one per test."""
    if run_many and each:
        return [run_many.replace("{tests}", " ".join(command_for(each, t) for t in tests))]
    return [command_for(run, t).replace("{job}", "0") for t in tests]


# ---------------------------------------------------------------- gcov


@dataclass
class Gcov:
    argv: list[str]  # e.g. ["gcov"] or ["llvm-cov", "gcov"]
    flavour: str  # "llvm" | "gnu"
    version: str


def find_gcov(configured: str = "") -> Gcov:
    """The gcov to use: the configured one, else `gcov` (Apple's is LLVM's), else `llvm-cov gcov`."""
    choices = [shlex.split(configured)] if configured else [["gcov"], ["llvm-cov", "gcov"]]
    for argv in choices:
        if not shutil.which(argv[0]):
            continue
        try:
            r = subprocess.run([*argv, "--version"], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            continue
        out = (r.stdout + r.stderr).strip()
        first = out.splitlines()[0] if out else " ".join(argv)
        return Gcov(argv, "llvm" if "LLVM" in out else "gnu", first)
    raise ConfigError(
        "no gcov found. Install gcov (with GCC) or llvm-cov, or set `gcov = ...` in the config."
    )


def run_gcov(g: Gcov, gcda: Path, workdir: Path) -> tuple[list[cov.GcovFn], str]:
    """Functions from one .gcda (its .gcno must sit next to it). gcov runs in `workdir`, because it writes
    its output files into the current folder. Returns (functions, gcov's warnings)."""
    import gzip

    workdir.mkdir(parents=True, exist_ok=True)
    if g.flavour == "llvm":
        argv = [*g.argv, "-i", "-m", str(gcda)]
    else:
        argv = [*g.argv, "--json-format", "--stdout", str(gcda)]
    r = subprocess.run(argv, cwd=workdir, capture_output=True, text=True, timeout=600)
    warn = r.stderr
    if r.returncode != 0 and not r.stdout:
        warn += f"\n{' '.join(argv[:-1])} failed (exit {r.returncode})"
    fns: list[cov.GcovFn] = []
    if g.flavour == "gnu":
        fns = cov.parse_gnu_json(r.stdout)
        for out in sorted(workdir.glob("*.gcov.json.gz")):  # older GNU gcov ignores --stdout
            fns += cov.parse_gnu_json(gzip.decompress(out.read_bytes()).decode("utf-8", "replace"))
            out.unlink()
        return fns, warn
    for out in sorted(workdir.glob("*.gcov")):
        fns += cov.parse_llvm_intermediate(out.read_text(errors="replace"))
        out.unlink()
    return fns, warn


# ---------------------------------------------------------------- the plan and the run


@dataclass
class Plan:
    config: Config
    tests: list[str]  # empty until the `list` command ran (it runs only after the user said yes)
    only: list[str]
    gcov: Gcov
    env_removed: list[str]


def make_plan(c: Config, only: list[str]) -> Plan:
    """What will run. Nothing is run here: a `list` command is part of the plan, not run to make it."""
    tests = [] if c.list_cmd else sorted(set(c.tests))
    if only and tests:
        unknown = sorted(set(only) - set(tests))
        if unknown:
            raise ConfigError(f"not in the test list: {', '.join(unknown)}")
        tests = sorted(set(only))
    if not c.list_cmd and not tests:
        raise ConfigError("the test list is empty")
    removed = [k for k in ("GCOV_PREFIX", "GCOV_PREFIX_STRIP") if k in os.environ]
    return Plan(c, tests, sorted(set(only)), find_gcov(c.gcov), removed)


def list_tests(root: Path, p: Plan, log: Callable[[str], None]) -> None:
    """Run the config's `list` command (after the user agreed) and fill in the tests."""
    c = p.config
    if not c.list_cmd:
        return
    log(f"Listing tests: {c.list_cmd}")
    r = subprocess.run(c.list_cmd, shell=True, cwd=root, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise ConfigError(f"the `list` command failed (exit {r.returncode}): {r.stderr.strip()[:300]}")
    tests = sorted({ln.strip() for ln in r.stdout.splitlines() if ln.strip()})
    if p.only:
        unknown = sorted(set(p.only) - set(tests))
        if unknown:
            raise ConfigError(f"not in the test list: {', '.join(unknown)}")
        tests = p.only
    if not tests:
        raise ConfigError("the `list` command printed no tests")
    p.tests = tests
    log(f"{len(tests)} test(s), run like this:")
    for t in tests:
        log(f"  {command_for(c.run, t)}")


def plan_text(root: Path, p: Plan, jobs: int) -> str:
    c = p.config
    lines = [
        f"Config: {c.path} (SHA-256 {c.sha256})",
        "This config can run any command. Read it if it came with the code (a clone, a patch).",
        f"In folder: {root}",
    ]
    if c.build:
        lines.append(f"First, the build: {c.build}")
    if c.list_cmd:
        lines.append(f"Then, to list the tests: {c.list_cmd}")
        lines.append(f"Then every listed test{' named with --only' if p.only else ''}, {jobs} at a time, as:")
        lines.append(f"  {c.run}   ({{test}} = the test id, quoted for the shell)")
    else:
        lines.append(f"Then {len(p.tests)} test(s), {jobs} at a time, each with its own coverage folder:")
        lines += [f"  {command_for(c.run, t)}" for t in p.tests]
    lines.append(f"Coverage is read with: {' '.join(p.gcov.argv)} ({p.gcov.version})")
    lines.append(f"Results go to {cov.path(root)}; nothing else in the repo is written by AeroTrace.")
    if p.env_removed:
        lines.append(f"Removed from the environment for these runs: {', '.join(p.env_removed)}.")
    return "\n".join(lines)


@dataclass
class Result:
    test: str
    exit: str
    fns: list[tuple[str, cov.GcovFn]]  # (folder of the .gcno, function)
    newer: set[str]  # sources gcov said are newer than their .gcno


def _env(prefix: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("GCOV_PREFIX", "GCOV_PREFIX_STRIP")}
    env["GCOV_PREFIX"] = str(prefix)
    env["GCOV_PREFIX_STRIP"] = "0"
    return env


def _gcno_for(gcda: Path, prefix: Path) -> Path | None:
    """The .gcno next to the object the .gcda belongs to (the .gcda path is prefix + the object's path)."""
    orig = Path("/") / gcda.relative_to(prefix)
    for cand in (orig, Path(str(orig).replace("/private/", "/", 1))):
        g = cand.with_suffix(".gcno")
        if g.is_file():
            return g
    return None


def run_one(root: Path, p: Plan, test: str, job: int, log: Callable[[str], None]) -> Result:
    prefix = Path(tempfile.mkdtemp(prefix="aerotrace-cov-"))
    try:
        cmd = command_for(p.config.run, test, job)
        try:
            r = subprocess.run(
                cmd, shell=True, cwd=root, env=_env(prefix), timeout=p.config.timeout,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )  # fmt: skip
            code = str(r.returncode) if r.returncode >= 0 else f"signal {-r.returncode}"
        except subprocess.TimeoutExpired:
            code = "timeout"
        fns: list[tuple[str, cov.GcovFn]] = []
        newer: set[str] = set()
        for gcda in sorted(prefix.rglob("*.gcda")):
            gcno = _gcno_for(gcda, prefix)
            if gcno is None:
                continue
            link = gcda.with_suffix(".gcno")
            if not link.exists():
                os.symlink(gcno, link)
            work = prefix / ".out" / gcda.relative_to(prefix).with_suffix("")
            got, warn = run_gcov(p.gcov, gcda, work)
            fns += [(str(gcno.parent), f) for f in got]
            for line in warn.splitlines():
                # "source file is newer than notes file": the binary was built from other code
                if "newer than" in line and "'" in line:
                    newer.add(line.split("'")[1])
            if "failed (exit" in warn:
                log(f"  warning: gcov could not read {gcda.name}: {warn.strip().splitlines()[-1]}")
        log(f"  {test}: exit {code}, {sum(1 for _, f in fns if f.count)} functions ran")
        return Result(test, code, fns, newer)
    finally:
        shutil.rmtree(prefix, ignore_errors=True)


def record(
    root: Path,
    p: Plan,
    jobs: int,
    full: bool,
    log: Callable[[str], None],
    version: str,
) -> tuple[Path, cov.Coverage]:
    """Run the plan and write the coverage file. `full` = the whole list (no --only)."""
    from aerotrace.index.build import fresh_index
    from aerotrace.toolchain import git

    c = p.config
    if c.build:
        log(f"Building: {c.build}")
        sink = Path(tempfile.mkdtemp(prefix="aerotrace-build-"))  # counts from tools run during the build
        try:
            r = subprocess.run(c.build, shell=True, cwd=root, env=_env(sink))
        finally:
            shutil.rmtree(sink, ignore_errors=True)
        if r.returncode != 0:
            raise ConfigError(f"the build failed (exit {r.returncode}); nothing was recorded")
    list_tests(root, p, log)
    obj_dirs = [(root / o).resolve() for o in c.objects]
    if not any(any(d.rglob("*.gcno")) for d in obj_dirs if d.is_dir()):
        raise ConfigError(
            f"no .gcno files under {', '.join(c.objects)}. Build with coverage first "
            "(gcc/clang --coverage; for Redis: make -C src MALLOC=libc gcov)."
        )
    results: list[Result] = []
    interrupted = False
    try:
        with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
            futs = [pool.submit(run_one, root, p, t, i % max(1, jobs), log) for i, t in enumerate(p.tests)]
            for f in futs:
                results.append(f.result())
    except KeyboardInterrupt:
        interrupted = True
        log("Interrupted: saving what finished.")

    conn, _ = fresh_index(root)
    try:
        idx = cov.index_files(conn)
        by_hash = {r[1]: r[0] for r in conn.execute("SELECT path, hash FROM files")}
    finally:
        conn.close()
    out = cov.Coverage(
        complete=full and not interrupted and len(results) == len(p.tests),
        commit=_head(root, git),
        config_sha256=c.sha256,
        config=c.raw,
        gcov=p.gcov.version,
        run=c.run,
        run_many=c.run_many,
        each=c.each,
        aerotrace=version,
    )
    done = {r.test for r in results}
    out.tests = [cov.TestRun(r.test, r.exit) for r in results] + [
        cov.TestRun(t, "not run") for t in p.tests if t not in done
    ]
    dropped: dict[str, int] = {}
    for r in results:
        for gdir, f in r.fns:
            rel = cov.resolve_source(f.source, [Path(gdir), *obj_dirs], root, by_hash)
            if rel is None:
                dropped["outside the repo"] = dropped.get("outside the repo", 0) + 1
                continue
            ifile = idx.get(rel)
            if ifile is None:
                dropped["file not in the index"] = dropped.get("file not in the index", 0) + 1
                continue
            q = cov.match([ifile], f.name, f.line)
            if q is None:
                dropped["function not in the index"] = dropped.get("function not in the index", 0) + 1
                continue
            fc = out.files.get(rel)
            if fc is None:
                fc = out.files[rel] = cov.FileCoverage(cov.sha256_file(root / rel) or "")
            fc.functions.setdefault(q, set())
            fc.lines[q] = next(a for n, a, _ in ifile.functions if n == q)
            if f.count > 0:
                fc.functions[q].add(r.test)
        for s in r.newer:
            rel = cov.resolve_source(s, obj_dirs, root, by_hash)
            if rel and rel in out.files:
                out.files[rel].notes_older = True
    out.dropped = dropped
    out.inputs = {rel: cov.sha256_file(root / rel) or "" for rel in cov.expand_inputs(root, c.inputs)}
    return _save(root, out), out


def _head(root: Path, git) -> str | None:
    try:
        return git.resolve_base(root, "HEAD").sha
    except git.GitError:
        return None


def _save(root: Path, new: cov.Coverage) -> Path:
    """Write tests.json. A partial run never replaces a complete file: it is merged in when the recorded
    files and config are the same, else written to tests.partial.json."""
    if new.complete:
        p = cov.write(root, new)
        stale = cov.path(root, partial=True)
        if stale.is_file():
            stale.unlink()
        return p
    old, _ = cov.load(root)
    if old is not None and old.complete and _same_basis(root, old, new):
        rerun = {t.id for t in new.tests if t.exit != "not run"}
        merged = old
        merged.tests = [t for t in old.tests if t.id not in rerun] + [t for t in new.tests if t.id in rerun]
        for fc in merged.files.values():
            for tests in fc.functions.values():
                tests -= rerun
        for rel, fc in new.files.items():
            m = merged.files.setdefault(rel, cov.FileCoverage(fc.sha256))
            for q, tests in fc.functions.items():
                m.functions.setdefault(q, set()).update(tests & rerun)
                m.lines.setdefault(q, fc.lines.get(q, 0))
        merged.complete = all(t.exit != "not run" for t in merged.tests)
        return cov.write(root, merged)
    return cov.write(root, new, partial=True)


def _same_basis(root: Path, old: cov.Coverage, new: cov.Coverage) -> bool:
    if old.config_sha256 != new.config_sha256:
        return False
    for rel, fc in old.files.items():
        if cov.sha256_file(root / rel) != fc.sha256:
            return False
    return not cov.changed_inputs(root, old)


def confirm(text: str, yes: bool) -> bool:
    print(text, flush=True)
    if yes:
        print("Running (--yes).")
        return True
    if not sys.stdin.isatty():
        print("Not running: there is no terminal to ask. Pass --yes to run these commands.")
        return False
    try:
        return input("Run these? [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False
