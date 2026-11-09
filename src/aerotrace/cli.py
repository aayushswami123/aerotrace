"""Command-line entry point: `aerotrace <command>`."""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from pathlib import Path

from aerotrace import __version__


def find_root(given: str | None) -> Path:
    """The repo to work on: --root, else the git top level of the current folder, else the current folder."""
    if given:
        return Path(given).resolve()
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=10
        )
        if r.returncode == 0 and r.stdout.strip():
            return Path(r.stdout.strip()).resolve()
    except (OSError, subprocess.SubprocessError):
        pass
    return Path.cwd().resolve()


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


# ---------------------------------------------------------------- commands


def cmd_doctor(args: argparse.Namespace) -> int:
    from aerotrace.toolchain import compdb, libclang

    root = find_root(args.root)
    print(f"aerotrace {__version__}")
    print(f"python    {platform.python_version()} on {platform.system()} {platform.machine()}")
    try:
        choice = libclang.find_libclang()
        libclang.configure(choice)
        print(f"libclang  {libclang.clang_version()}")
        print(f"          from {choice.source}{f': {choice.path}' if choice.path else ''}")
    except Exception as e:  # report every problem in plain words; doctor must never crash
        print(f"libclang  NOT WORKING: {e}")
        return 1
    print(f"repo      {root}")
    try:
        db = compdb.find_compdb(root)
    except FileNotFoundError as e:
        db = None
        print(f"flags     {e}")
    print(
        f"flags     {db.relative_to(root) if db else 'no compile_commands.json found; flags will be guessed'}"
    )
    print(
        f"headers   {len(compdb.system_include_args('c'))} C and {len(compdb.system_include_args('c++'))} C++ folders"
    )
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    from aerotrace.index.build import build_index

    root = find_root(args.root)
    try:
        report = build_index(
            root,
            build_dir=Path(args.build_dir).resolve() if args.build_dir else None,
            excludes=args.exclude,
            jobs=args.jobs,
            use_cache=not args.no_cache,
            log=_err,
        )
    except FileNotFoundError as e:
        _err(f"error: {e}")
        return 2
    if args.json:
        print(json.dumps(report.__dict__, indent=2, sort_keys=True))
        return 0
    for f, errs in sorted(report.errors_by_file.items())[: args.show_errors]:
        _err(f"parse errors in {f}:")
        for e in errs[:3]:
            _err(f"  {e}")
    if report.flags_source == "guessed":
        _err("note: flags were guessed. For accurate results, point AeroTrace at a compile_commands.json")
        _err("      (CMake: -DCMAKE_EXPORT_COMPILE_COMMANDS=ON; Make: run your build under `bear`).")
    if report.clashes:
        _err(
            f"note: {report.clashes} functions share a clang ID with another definition; they are kept apart."
        )
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    from aerotrace.index import store

    root = find_root(args.root)
    try:
        conn = store.connect(root)
    except FileNotFoundError as e:
        _err(str(e))
        return 2
    meta = store.read_meta(conn)
    counts = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("functions", "variables", "edges", "blind_spots")
    }
    changed = store.changed_files(conn, root)
    out = {
        **meta,
        **counts,
        "changed_since_index": len(changed),
        "changed_files": changed[:20],
    }
    if args.json:
        print(json.dumps(out, indent=2, sort_keys=True))
    else:
        for k, v in out.items():
            print(f"{k:22} {v}")
    return 0


# ---------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="aerotrace", description="Change impact for C and C++ code, for AI agents."
    )
    p.add_argument("--version", action="version", version=f"aerotrace {__version__}")
    sub = p.add_subparsers(dest="command")

    def with_root(sp: argparse.ArgumentParser) -> argparse.ArgumentParser:
        sp.add_argument("--root", help="repo folder (default: the git top level of the current folder)")
        return sp

    d = with_root(sub.add_parser("doctor", help="check libclang, compile flags and headers"))
    d.set_defaults(func=cmd_doctor)

    i = with_root(sub.add_parser("index", help="build or refresh the index in .aerotrace/"))
    i.add_argument("--build-dir", help="folder holding compile_commands.json")
    i.add_argument("--exclude", action="append", default=[], metavar="GLOB", help="path to skip (repeatable)")
    i.add_argument("--jobs", type=int, help="parallel parsers (default: CPU count)")
    i.add_argument("--no-cache", action="store_true", help="re-parse every file")
    i.add_argument("--show-errors", type=int, default=10, metavar="N", help="list parse errors for N files")
    i.add_argument("--json", action="store_true", help="print the build report as JSON")
    i.set_defaults(func=cmd_index)

    s = with_root(sub.add_parser("status", help="what is in the index and whether it is up to date"))
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_status)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
