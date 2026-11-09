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


def _open(args: argparse.Namespace):
    from aerotrace.index import store

    root = find_root(args.root)
    try:
        return store.connect(root)
    except FileNotFoundError as e:
        _err(str(e))
        return None


def _resolve_one(conn, query: str):
    """The single function `query` names, or None after explaining the choices."""
    from aerotrace.analysis import render, symbols

    fns = symbols.find(conn, query)
    if len(fns) == 1:
        return fns[0]
    _err(render.candidates_text(query, fns or symbols.search(conn, query)))
    return None


def cmd_find(args: argparse.Namespace) -> int:
    from aerotrace.analysis import symbols

    conn = _open(args)
    if conn is None:
        return 2
    fns = symbols.find(conn, args.query) or symbols.search(conn, args.query, args.limit)
    if args.json:
        print(json.dumps([f.brief() for f in fns[: args.limit]], indent=2))
    else:
        for f in fns[: args.limit]:
            print(f"{f.qualified_name}@{f.file}  {f.where}  {f.signature}")
    return 0 if fns else 1


def cmd_walk(args: argparse.Namespace) -> int:
    from aerotrace.analysis import graph, render

    conn = _open(args)
    if conn is None:
        return 2
    fn = _resolve_one(conn, args.symbol)
    if fn is None:
        return 1
    w = graph.walk(conn, fn, args.command, args.depth)
    print(
        json.dumps(render.walk_json(w, args.limit), indent=2)
        if args.json
        else render.walk_text(w, args.limit)
    )
    return 0


def cmd_impact(args: argparse.Namespace) -> int:
    from aerotrace.analysis import graph, render

    conn = _open(args)
    if conn is None:
        return 2
    fn = _resolve_one(conn, args.symbol)
    if fn is None:
        return 1
    imp = graph.impact(conn, fn, args.depth)
    print(
        json.dumps(render.impact_json(imp, args.limit), indent=2)
        if args.json
        else render.impact_text(imp, args.limit)
    )
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    from aerotrace.analysis import diff, render
    from aerotrace.index.build import fresh_index
    from aerotrace.index.history import Outliner
    from aerotrace.toolchain.git import GitError

    root = find_root(args.root)
    build_dir = Path(args.build_dir).resolve() if args.build_dir else None
    outliner = Outliner(root, build_dir)
    try:
        if args.patch:
            text = sys.stdin.read() if args.patch == "-" else Path(args.patch).read_text(encoding="utf-8")
            mode, base, diffs, old = "patch", None, diff.load_patch(root, text), {}
        else:
            mode = "git"
            diffs, base, old = diff.load_git(root, args.base, outliner)
    except (GitError, ValueError, OSError) as e:
        _err(f"error: {e}")
        return 2
    try:
        conn, note = fresh_index(root, build_dir=build_dir, want=diff.wanted(diffs, mode))
    except FileNotFoundError as e:
        _err(f"error: {e}")
        return 2
    if note:
        _err(note)
    res = diff.analyse(conn, root, diffs, mode=mode, base=base, depth=args.depth, old=old, now=outliner.now)
    print(
        json.dumps(render.diff_json(res, args.limit), indent=2)
        if args.json
        else render.diff_text(res, args.limit)
    )
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    from aerotrace.mcp.server import run

    run(find_root(args.root), Path(args.build_dir).resolve() if args.build_dir else None)
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    from aerotrace.mcp import install

    root = find_root(args.root)
    try:
        p = install.plan(args.client, root)
    except install.InstallError as e:
        _err(f"error: {e}")
        return 2
    if args.dry_run:
        print(f"Would write {p.path}:\n{p.content}")
        return 0
    install.apply(p)
    print(f"Added AeroTrace to {p.path}")
    print(p.note)
    try:
        from aerotrace.index import store

        store.connect(root).close()
    except FileNotFoundError:
        print(f"Tip: run `aerotrace index` in {root} now, so the first question is answered quickly.")
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

    def query(name: str, help_: str, func, symbol: bool = True) -> None:
        q = with_root(sub.add_parser(name, help=help_))
        q.add_argument(
            "symbol" if symbol else "query",
            help="function name, ns::Class::method, name@file, or file.c:LINE",
        )
        if symbol:
            q.add_argument("--depth", type=int, default=5, help="how many call steps to follow (default 5)")
        q.add_argument("--limit", type=int, default=50, help="most results to print (default 50)")
        q.add_argument("--json", action="store_true")
        q.set_defaults(func=func)

    query("find", "find functions by name or file:line", cmd_find, symbol=False)
    query("callers", "who calls this function, directly or through others", cmd_walk)
    query("callees", "what this function calls, directly or through others", cmd_walk)
    query("impact", "what could break if this function changes (callers + data readers)", cmd_impact)

    df = with_root(
        sub.add_parser(
            "diff",
            help="what a change could break: the working tree vs a commit or branch, or a patch not yet applied",
        )
    )
    df.add_argument(
        "base",
        nargs="?",
        default="HEAD",
        help="commit or branch to compare the working tree with (default HEAD). For a branch such as "
        "`main`, AeroTrace compares with the point where the current branch left it.",
    )
    df.add_argument("--patch", metavar="FILE", help="a unified diff that is not applied yet ('-' = stdin)")
    df.add_argument("--build-dir", help="folder holding compile_commands.json")
    df.add_argument("--depth", type=int, default=5, help="how many call steps to follow (default 5)")
    df.add_argument("--limit", type=int, default=50, help="most results per list (default 50)")
    df.add_argument("--json", action="store_true")
    df.set_defaults(func=cmd_diff)

    m = with_root(sub.add_parser("mcp", help="run the MCP server on stdin/stdout (agents start this)"))
    m.add_argument("--build-dir", help="folder holding compile_commands.json")
    m.set_defaults(func=cmd_mcp)

    ins = with_root(sub.add_parser("install", help="add AeroTrace to Claude Code, Cursor or Codex"))
    ins.add_argument("--client", required=True, choices=["claude", "cursor", "codex"])
    ins.add_argument("--dry-run", action="store_true", help="show what would be written, change nothing")
    ins.set_defaults(func=cmd_install)

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
