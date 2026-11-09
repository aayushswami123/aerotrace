"""Build the index: find flags, parse every file in parallel (with a cache), merge, and store."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import sqlite3
import threading
from collections import Counter
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from multiprocessing import get_context
from pathlib import Path

from aerotrace import __version__
from aerotrace.frontends.cpp import parse as parse_mod
from aerotrace.index import store
from aerotrace.index.merge import merge
from aerotrace.toolchain import libclang
from aerotrace.toolchain.compdb import (
    SOURCE_EXTS,
    CompileUnit,
    find_compdbs,
    find_units,
    fingerprint,
    system_include_args,
    unit_language,
)

PARSER_VERSION = "11"  # bump when parse output changes, so old cache entries are ignored


@dataclass
class BuildReport:
    root: str
    flags_source: str
    compdb_path: str | None
    libclang: str
    libclang_source: str
    units: int = 0
    cache_hits: int = 0
    files_with_errors: int = 0
    errors_by_file: dict[str, list[str]] = field(default_factory=dict)
    multi_config_files: int = 0
    unsafe_flags_removed: int = 0
    functions: int = 0
    variables: int = 0
    edges: int = 0
    blind_spots: int = 0
    clashes: int = 0
    alternatives: list[str] = field(default_factory=list)  # other compile databases found, not used
    targets: list[str] = field(default_factory=list)  # cross-compile targets taken from compiler names
    response_files_skipped: list[str] = field(default_factory=list)
    not_in_compdb: list[str] = field(default_factory=list)  # repo sources the database does not list
    ignored_flags: dict[str, int] = field(default_factory=dict)  # flag clang does not know -> files
    machine_flags_dropped: int = 0  # files parsed again without -m flags after libclang refused them
    missing_headers: dict[str, int] = field(default_factory=dict)  # header -> files that could not find it


@dataclass(frozen=True)
class Progress:
    """How far a build is. `done` counts files finished (reused from the cache or parsed)."""

    phase: str  # "flags", "cache", "parse", "save"
    done: int
    total: int
    reused: int


class NoSourcesError(SystemExit):
    """There is nothing to index. A SystemExit, so the CLI prints the message and stops as before."""


class SetupError(FileNotFoundError):
    """Something the build needs is missing (found before building). The message says what to do."""


class LibclangLoadError(RuntimeError):
    """libclang could not be loaded."""


class ParserCrashed(RuntimeError):
    """A parser process died (a libclang crash, or the system killed it for memory)."""

    def __init__(self, unfinished: list[str]):
        self.unfinished = len(unfinished)  # files not parsed; the one that crashed is among them
        self.files = unfinished[:5]  # the first few, sorted
        super().__init__(f"a parser process crashed; {self.unfinished} files were not parsed")


class BuildStopped(Exception):
    """The build was asked to stop (the server is shutting down)."""


# ---------------------------------------------------------------- cache


def _cache_key(unit: CompileUnit, extra: list[str], excludes: list[str], clang: str) -> str:
    blob = json.dumps([PARSER_VERSION, clang, unit.directory, unit.args, extra, sorted(excludes)])
    return hashlib.sha256(blob.encode()).hexdigest()


def _cache_file(cache_dir: Path, unit: CompileUnit) -> Path:
    return cache_dir / (hashlib.sha256(unit.file.encode()).hexdigest()[:32] + ".json")


def _cache_load(path: Path, key: str, root: Path) -> dict | None:
    """A cached result counts only if the key matches and every file it read is unchanged. The files'
    size and mtime are taken again now, so the index records when they were last checked."""
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
        if entry["key"] != key:
            return None
        result = entry["result"]
        stats = {}
        for dep, h in result["deps"].items():
            now = parse_mod.file_stamp(str(root / dep), h)
            if not h or now[0] != h:
                return None
            stats[dep] = [now[1], now[2]]
        result["stats"] = stats
        return result
    except (OSError, ValueError, KeyError):
        return None


def _cache_save(path: Path, key: str, result: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp{os.getpid()}")
    tmp.write_text(json.dumps({"key": key, "result": result}), encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------- workers


def _init_worker(choice: libclang.LibclangChoice) -> None:
    # Ctrl-C in a terminal reaches the whole process group; the parent ends the parsers itself, so they
    # ignore it (no tracebacks from every parser).
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    libclang.configure(choice)


def _work(job: tuple) -> dict:
    unit, root, excludes, extra = job
    return parse_mod.parse_unit(unit, Path(root), excludes, extra)


def _top(counts: dict[str, int], n: int = 10) -> list[list]:
    """The `n` most common entries, most common first, ties by name: [[name, count], ...]."""
    return [[k, v] for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]


# ---------------------------------------------------------------- entry point


def build_index(
    root: Path,
    *,
    build_dir: Path | None = None,
    excludes: list[str] | None = None,
    jobs: int | None = None,
    use_cache: bool = True,
    log: Callable[[str], None] = lambda _msg: None,
    progress: Callable[[Progress], None] = lambda _p: None,
    isolate: bool = False,
    stop: threading.Event | None = None,
) -> BuildReport:
    """Build and store the index. `progress` is called as files finish (from this thread; keep it quick).
    `isolate` parses in child processes even with one job, so a libclang crash cannot take this process
    down. Setting `stop` makes the build give up soon with BuildStopped, writing nothing."""
    root = Path(os.path.realpath(root))
    excludes = list(excludes or [])

    def check_stop() -> None:
        if stop is not None and stop.is_set():
            raise BuildStopped()

    try:
        choice = libclang.configure()
        clang = libclang.clang_version()
    except FileNotFoundError:
        raise
    except Exception as e:  # clang.cindex.LibclangError, or OSError from dlopen
        raise LibclangLoadError(str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__) from e

    flags = find_units(root, build_dir, excludes)
    if not flags.units:
        raise NoSourcesError(f"no C or C++ source files found under {root}")
    report = BuildReport(
        root=str(root),
        flags_source=flags.source,
        compdb_path=flags.path,
        libclang=clang,
        libclang_source=choice.source,
        units=len(flags.units),
        multi_config_files=sum(1 for u in flags.units if u.configs > 1),
        unsafe_flags_removed=flags.unsafe_flags_removed,
        alternatives=flags.alternatives,
        targets=flags.targets,
        response_files_skipped=flags.response_files_skipped,
        not_in_compdb=flags.not_in_compdb,
    )
    where = f"from {flags.path}" if flags.path else "guessed (no compile_commands.json found)"
    log(f"{len(flags.units)} source files, flags {where}")
    log(f"libclang: {clang} ({choice.source})")

    total = len(flags.units)
    progress(Progress("flags", 0, total, 0))
    check_stop()

    cache_dir = store.ensure_index_dir(root) / "cache"
    results: dict[str, dict] = {}
    todo: list[tuple[CompileUnit, str, list[str]]] = []
    for u in flags.units:
        extra = system_include_args(unit_language(u))
        key = _cache_key(u, extra, excludes, clang)
        hit = _cache_load(_cache_file(cache_dir, u), key, root) if use_cache else None
        if hit is not None:
            results[u.file] = hit
            report.cache_hits += 1
        else:
            todo.append((u, key, extra))
    log(f"cache: {report.cache_hits} reused, {len(todo)} to parse")
    done = report.cache_hits
    progress(Progress("cache", done, total, report.cache_hits))
    check_stop()

    def finished(u: CompileUnit, key: str, res: dict) -> None:
        nonlocal done
        results[u.file] = res
        _cache_save(_cache_file(cache_dir, u), key, res)
        done += 1
        progress(Progress("parse", done, total, report.cache_hits))

    jobs = max(1, min(jobs or os.cpu_count() or 1, len(todo) or 1))
    if todo and jobs == 1 and not isolate:
        for u, key, extra in todo:
            check_stop()
            finished(u, key, _work((u, str(root), excludes, extra)))
    elif todo:
        _parse_in_pool(root, todo, excludes, jobs, choice, finished, stop)

    missing = [u.file for u in flags.units if u.file not in results]
    if missing:  # never write an index with files left out
        raise RuntimeError(f"{len(missing)} files have no parse result, for example {missing[0]}")
    check_stop()
    progress(Progress("save", total, total, report.cache_hits))

    ordered = [results[u.file] for u in flags.units]  # units are sorted by path
    ignored: Counter[str] = Counter()
    missing: Counter[str] = Counter()
    for res in ordered:
        if res["error_count"]:
            report.files_with_errors += 1
            report.errors_by_file[res["file"]] = res["errors"]
        ignored.update(res.get("ignored_flags", []))
        missing.update(res.get("missing_headers", []))
        report.machine_flags_dropped += bool(res.get("dropped_flags"))
    report.ignored_flags = dict(sorted(ignored.items()))
    report.missing_headers = dict(sorted(missing.items()))

    graph = merge(ordered)
    report.functions = len(graph.functions)
    report.variables = len(graph.variables)
    report.edges = len(graph.edges)
    report.blind_spots = len(graph.blind_spots)
    report.clashes = sum(1 for f in graph.functions if f["clash"])

    configs = {parse_mod.rel_to(u.file, root): u.configs for u in flags.units}
    files: dict[str, dict] = {}
    for res in ordered:
        for dep, h in res["deps"].items():
            if dep in files:
                continue
            # size and mtime as they were when the file was hashed, not now: a file edited since the
            # parse must not look unchanged (#43)
            size, mtime = res.get("stats", {}).get(dep, (-1, -1))
            files[dep] = {
                "path": dep,
                "hash": h,
                "size": size,
                "mtime_ns": mtime,
                "is_unit": 0,
                "error_count": 0,
                "errors_json": "[]",
                "configs": 1,
            }
        f = files.get(res["file"])
        if f is None:  # the source file is missing, so there is nothing to track
            continue
        f.update(
            is_unit=1,
            error_count=res["error_count"],
            errors_json=store.errors_json(res["errors"]),
            configs=configs.get(res["file"], 1),
        )

    meta = {
        "aerotrace_version": __version__,
        "parser_version": PARSER_VERSION,
        "libclang": clang,
        "libclang_source": choice.source,
        "flags_source": flags.source,
        "compdb_path": flags.path or "",
        "units": str(report.units),
        "files_with_errors": str(report.files_with_errors),
        "multi_config_files": str(report.multi_config_files),
        # how this index was built, so a refresh uses the same settings
        "build_dir": str(build_dir) if build_dir else "",
        "excludes": json.dumps(sorted(excludes)),
        "compdb_hash": flags.hash,
        # what the flags could not cover (shown as limits of every answer)
        "compdb_alternatives": json.dumps(report.alternatives),
        "targets": json.dumps(report.targets),
        "not_in_compdb": json.dumps(report.not_in_compdb),
        "missing_headers": json.dumps(_top(report.missing_headers)),
        "ignored_flags": json.dumps(_top(report.ignored_flags)),
        "machine_flags_dropped": str(report.machine_flags_dropped),
    }
    store.write_index(root, graph, [files[k] for k in sorted(files)], meta)
    log(
        f"indexed {report.functions} functions, {report.variables} variables, {report.edges} links; "
        f"{report.files_with_errors} of {report.units} files had parse errors"
    )
    return report


def _parse_in_pool(
    root: Path,
    todo: list[tuple[CompileUnit, str, list[str]]],
    excludes: list[str],
    jobs: int,
    choice: libclang.LibclangChoice,
    finished: Callable[[CompileUnit, str, dict], None],
    stop: threading.Event | None,
    work: Callable[[tuple], dict] = _work,
) -> None:
    """Parse `todo` in `jobs` child processes, calling `finished` for each file as it is done."""
    pool = ProcessPoolExecutor(
        jobs, mp_context=get_context("spawn"), initializer=_init_worker, initargs=(choice,)
    )
    futures: dict[Future, tuple[CompileUnit, str]] = {
        pool.submit(work, (u, str(root), excludes, extra)): (u, key) for u, key, extra in todo
    }
    pending = set(futures)
    clean = False
    try:
        while pending:
            if stop is not None and stop.is_set():
                raise BuildStopped()
            ready, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
            crashed = False
            for fut in sorted(ready, key=lambda f: futures[f][0].file):  # same order every time
                try:
                    res = fut.result()
                except BrokenProcessPool:
                    crashed = True
                    continue
                finished(*futures[fut], res)
            if crashed:
                # keep what the other parsers finished, so a retry starts from there
                for fut in sorted(pending, key=lambda f: futures[f][0].file):
                    if fut.done() and not fut.cancelled() and fut.exception() is None:
                        finished(*futures[fut], fut.result())
                raise ParserCrashed(
                    sorted(
                        parse_mod.rel_to(futures[f][0].file, root) or futures[f][0].file
                        for f in futures
                        if not (f.done() and not f.cancelled() and f.exception() is None)
                    )
                )
        clean = True
    finally:
        if clean:
            pool.shutdown()
        else:  # stopped or crashed: end the parsers now and drop queued work
            # (shutdown() forgets the processes, so take them first; there is no public way to do this)
            procs = list((getattr(pool, "_processes", None) or {}).values())
            for proc in procs:
                proc.terminate()
            pool.shutdown(wait=False, cancel_futures=True)
            for proc in procs:
                proc.join(5)


def remembered(root: Path) -> tuple[Path | None, list[str]]:
    """(build dir, excludes) the current index was built with, or (None, []) when there is no index.
    Read from an index of any version, so an upgrade keeps the user's --build-dir and --exclude."""
    meta = store.peek_meta(root)
    build_dir = Path(meta["build_dir"]) if meta.get("build_dir") else None
    try:
        excludes = json.loads(meta.get("excludes") or "[]")
    except ValueError:
        excludes = []
    return build_dir, excludes


def flags_changed(root: Path, meta: dict[str, str], build_dir: Path | None) -> str:
    """Why the compile flags no longer match the index ("" when they still do)."""
    try:
        found = find_compdbs(root, build_dir)
    except FileNotFoundError:
        return f"compile_commands.json is gone from {build_dir}"
    if not found:
        return "compile_commands.json was removed" if meta.get("compdb_hash") else ""
    if fingerprint(found[0]) != meta.get("compdb_hash"):
        return (
            "compile_commands.json changed" if meta.get("compdb_hash") else "a compile_commands.json appeared"
        )
    return ""


@dataclass(frozen=True)
class RefreshPlan:
    """Why the index must be (re)built, and with which settings."""

    kind: str  # "new", "version", "flags", "files"
    reason: str  # short, for progress messages: "no index yet", "3 changed file(s): a.c, b.c, c.c"
    build_dir: Path | None
    excludes: list[str]
    detail: str = ""  # the full reason for the note (version and flags)
    missing: tuple[str, ...] = ()  # wanted source files that are not in the index yet
    changed: tuple[str, ...] = ()  # indexed files that changed or are gone

    def note(self, report: BuildReport) -> str:
        """One line for the agent about what was done."""
        if self.kind == "new":
            return f"(Built the index: {report.functions} functions from {report.units} files.)"
        if self.kind == "version":
            return f"(Rebuilt the index: {self.detail}. Now {report.functions} functions from {report.units} files.)"
        if self.kind == "flags":
            return f"(Re-indexed: {self.detail}.)"
        return f"(Re-indexed {self.reason}.)"


def _check_setup(build_dir: Path | None) -> None:
    """Fail early, with what to do, when something the build needs is missing."""
    env = os.environ.get(libclang.ENV_VAR)
    if env and not Path(env).is_file():
        raise SetupError(f"{libclang.ENV_VAR} points to {env}, which does not exist. Fix or unset it.")
    if build_dir is not None and not (build_dir / "compile_commands.json").is_file():
        raise SetupError(
            f"no compile_commands.json in {build_dir}. Fix --build-dir, or create it "
            f"(CMake: cmake -B {build_dir} -DCMAKE_EXPORT_COMPILE_COMMANDS=ON)."
        )


def index_plan(
    root: Path, *, build_dir: Path | None = None, want: list[str] | tuple[str, ...] = ()
) -> RefreshPlan | None:
    """None when the index is fresh; otherwise why and how to rebuild it. Raises SetupError when a rebuild
    is needed but cannot work (see _check_setup). Opens and closes its own connection.

    A refresh uses the build dir and excludes the index was built with, unless `build_dir` is given.
    `want` lists repo-relative source files the caller needs in the index (for example files a diff
    touches). When flags are guessed, a new source file is picked up by re-indexing.
    """
    try:
        conn = store.connect(root)
    except store.IndexVersionError as e:
        old_dir, excludes = remembered(root)  # rebuild with the settings the old index was made with
        if build_dir is None and old_dir is not None and (old_dir / "compile_commands.json").is_file():
            build_dir = old_dir
        _check_setup(build_dir)
        why = "the old index can't be used (another AeroTrace version, or damaged)"
        return RefreshPlan("version", why, build_dir, excludes, detail=e.reason)
    except FileNotFoundError:
        _check_setup(build_dir)
        return RefreshPlan("new", "no index yet", build_dir, [])
    try:
        meta = store.read_meta(conn)
        excludes = json.loads(meta.get("excludes") or "[]")
        if build_dir is None and meta.get("build_dir"):
            build_dir = Path(meta["build_dir"])
            if not (build_dir / "compile_commands.json").is_file():
                build_dir = None  # it is gone; find a database the usual way (flags_changed explains)
        why_flags = flags_changed(root, meta, build_dir)
        changed = store.changed_files(conn, root)
        missing: list[str] = []
        if want and meta.get("flags_source") == "guessed":
            known = store.indexed_paths(conn)
            missing = sorted(
                p
                for p in want
                if p not in known and Path(p).suffix.lower() in SOURCE_EXTS and (root / p).is_file()
            )
    finally:
        conn.close()
    if not changed and not missing and not why_flags:
        return None
    _check_setup(build_dir)
    if why_flags:
        return RefreshPlan("flags", why_flags, build_dir, excludes, detail=why_flags, missing=tuple(missing))
    stale = changed + missing
    shown = ", ".join(stale[:3]) + (" ..." if len(stale) > 3 else "")
    return RefreshPlan(
        "files",
        f"{len(stale)} changed file(s): {shown}",
        build_dir,
        excludes,
        missing=tuple(missing),
        changed=tuple(changed),
    )


def fresh_index(
    root: Path,
    *,
    build_dir: Path | None = None,
    want: list[str] | tuple[str, ...] = (),
    log: Callable[[str], None] = lambda _msg: None,
) -> tuple[sqlite3.Connection, str]:
    """Open the index, building or refreshing it first when files or compile flags changed. Returns
    (connection, note), where the note says in one line what was done ("" when nothing was).
    See index_plan for the details."""
    plan = index_plan(root, build_dir=build_dir, want=want)
    if plan is None:
        return store.connect(root), ""
    report = build_index(root, build_dir=plan.build_dir, excludes=plan.excludes, log=log)
    return store.connect(root), plan.note(report)
