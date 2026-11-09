"""Build the index: find flags, parse every file in parallel (with a cache), merge, and store."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
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

PARSER_VERSION = "5"  # bump when parse output changes, so old cache entries are ignored


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


# ---------------------------------------------------------------- cache


def _cache_key(unit: CompileUnit, extra: list[str], excludes: list[str], clang: str) -> str:
    blob = json.dumps([PARSER_VERSION, clang, unit.directory, unit.args, extra, sorted(excludes)])
    return hashlib.sha256(blob.encode()).hexdigest()


def _cache_file(cache_dir: Path, unit: CompileUnit) -> Path:
    return cache_dir / (hashlib.sha256(unit.file.encode()).hexdigest()[:32] + ".json")


def _cache_load(path: Path, key: str, root: Path) -> dict | None:
    """A cached result counts only if the key matches and every file it read is unchanged."""
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
        if entry["key"] != key:
            return None
        for dep, h in entry["result"]["deps"].items():
            if parse_mod.file_hash(str(root / dep)) != h:
                return None
        return entry["result"]
    except (OSError, ValueError, KeyError):
        return None


def _cache_save(path: Path, key: str, result: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp{os.getpid()}")
    tmp.write_text(json.dumps({"key": key, "result": result}), encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------- workers


def _init_worker(choice: libclang.LibclangChoice) -> None:
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
) -> BuildReport:
    root = Path(os.path.realpath(root))
    excludes = list(excludes or [])
    choice = libclang.configure()
    clang = libclang.clang_version()

    flags = find_units(root, build_dir, excludes)
    if not flags.units:
        raise SystemExit(f"no C or C++ source files found under {root}")
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

    jobs = max(1, min(jobs or os.cpu_count() or 1, len(todo) or 1))
    job_args = [(u, str(root), excludes, extra) for u, _, extra in todo]
    if jobs == 1:
        outputs = map(_work, job_args)
        pool = None
    else:
        pool = ProcessPoolExecutor(
            jobs, mp_context=get_context("spawn"), initializer=_init_worker, initargs=(choice,)
        )
        outputs = pool.map(_work, job_args, chunksize=4)
    try:
        for (u, key, _), res in zip(todo, outputs, strict=True):
            results[u.file] = res
            _cache_save(_cache_file(cache_dir, u), key, res)
    finally:
        if pool is not None:
            pool.shutdown()

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
            st = (root / dep).stat()
            files[dep] = {
                "path": dep,
                "hash": h,
                "size": st.st_size,
                "mtime_ns": st.st_mtime_ns,
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


def remembered(root: Path) -> tuple[Path | None, list[str]]:
    """(build dir, excludes) the current index was built with, or (None, []) when there is no index."""
    try:
        conn = store.connect(root)
    except FileNotFoundError:
        return None, []
    try:
        meta = store.read_meta(conn)
    finally:
        conn.close()
    build_dir = Path(meta["build_dir"]) if meta.get("build_dir") else None
    return build_dir, json.loads(meta.get("excludes") or "[]")


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


def fresh_index(
    root: Path,
    *,
    build_dir: Path | None = None,
    want: list[str] | tuple[str, ...] = (),
    log: Callable[[str], None] = lambda _msg: None,
) -> tuple[sqlite3.Connection, str]:
    """Open the index, building or refreshing it first when files or compile flags changed. Returns
    (connection, note), where the note says in one line what was done ("" when nothing was).

    A refresh uses the build dir and excludes the index was built with, unless `build_dir` is given.
    `want` lists repo-relative source files the caller needs in the index (for example files a diff
    touches). When flags are guessed, a new source file is picked up by re-indexing.
    """
    try:
        conn = store.connect(root)
    except FileNotFoundError:
        report = build_index(root, build_dir=build_dir, log=log)
        return store.connect(
            root
        ), f"(Built the index: {report.functions} functions from {report.units} files.)"
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
    if not changed and not missing and not why_flags:
        return conn, ""
    conn.close()
    build_index(root, build_dir=build_dir, excludes=excludes, log=log)
    if why_flags:
        return store.connect(root), f"(Re-indexed: {why_flags}.)"
    stale = changed + missing
    shown = ", ".join(stale[:3]) + (" ..." if len(stale) > 3 else "")
    return store.connect(root), f"(Re-indexed {len(stale)} changed file(s): {shown}.)"
