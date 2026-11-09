"""Find compile flags for every source file.

Best: the project's own `compile_commands.json`. If there isn't one, we guess simple flags and say so.
We never run the user's build (cmake, make) ourselves.
"""

from __future__ import annotations

import fnmatch
import json
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

C_EXTS = {".c"}
CXX_EXTS = {".cc", ".cpp", ".cxx", ".c++", ".cp"}
SOURCE_EXTS = C_EXTS | CXX_EXTS
ALWAYS_SKIP_DIRS = {".git", ".aerotrace", ".hg", ".svn"}
GUESS_SKIP_DIRS = {"build", "out", "_build"}  # build outputs, only skipped when guessing

# Flags that would make clang load and run code. A crafted compile_commands.json must not be able to
# run anything on the user's machine (see SECURITY.md).
_UNSAFE_PREFIXES = ("-fplugin=", "-fpass-plugin=", "-fplugin-arg-")
_UNSAFE_XCLANG = {"-load", "-plugin", "-add-plugin"}
_DROP_WITH_VALUE = {"-o", "-MF", "-MT", "-MQ", "--serialize-diagnostics"}
_DROP_FLAGS = {"-c", "-MD", "-MMD", "-MP", "-MG", "-M", "-MM", "--"}
_WRAPPERS = {"ccache", "sccache", "distcc", "icecc"}


@dataclass
class CompileUnit:
    file: str  # absolute, real path of the source file
    directory: str  # working directory the args are relative to
    args: list[str] = field(default_factory=list)  # cleaned args, without compiler, -c, -o, source
    configs: int = 1  # how many entries the compile database had for this file (we parse the first)


@dataclass
class FlagsReport:
    source: str  # "compile_commands.json" or "guessed"
    path: str | None  # repo-relative path of the database, when there is one
    units: list[CompileUnit]
    skipped_outside_repo: int = 0
    unsafe_flags_removed: int = 0


def rel_to(path: str | Path, root: Path) -> str | None:
    """Repo-relative posix path, or None when `path` is outside the repo."""
    try:
        return Path(os.path.realpath(path)).relative_to(root).as_posix()
    except ValueError:
        return None


def is_excluded(rel: str, patterns: list[str]) -> bool:
    parts = rel.split("/")
    if any(p in ALWAYS_SKIP_DIRS for p in parts[:-1]):
        return True
    for pat in patterns:
        if pat.endswith("/"):
            if f"/{pat}" in f"/{rel}":
                return True
        elif fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(parts[-1], pat):
            return True
    return False


# ---------------------------------------------------------------- finding the database


def find_compdb(root: Path, build_dir: Path | None = None) -> Path | None:
    """The compile_commands.json to use: --build-dir, the repo root, then build folders."""
    if build_dir is not None:
        p = build_dir / "compile_commands.json"
        if not p.is_file():
            raise FileNotFoundError(f"no compile_commands.json in {build_dir}")
        return p
    if (root / "compile_commands.json").is_file():
        return root / "compile_commands.json"
    patterns = [
        "build*/compile_commands.json",
        "cmake-build-*/compile_commands.json",
        "out/*/compile_commands.json",
    ]
    hits = sorted({p for pat in patterns for p in root.glob(pat)})
    return hits[0] if hits else None


# ---------------------------------------------------------------- cleaning args


def clean_args(args: list[str], source_file: str, directory: str) -> tuple[list[str], int]:
    """Drop the compiler, -c, -o, dependency-file flags and the source file. Also drops unsafe flags.

    Returns (args, number of unsafe flags removed).
    """
    rest = list(args)
    if rest and Path(rest[0]).name in _WRAPPERS:
        rest = rest[1:]
    rest = rest[1:]  # the compiler itself
    src_real = os.path.realpath(os.path.join(directory, source_file))
    out: list[str] = []
    unsafe = 0
    i = 0
    while i < len(rest):
        a = rest[i]
        nxt = rest[i + 1] if i + 1 < len(rest) else None
        if a in _DROP_WITH_VALUE:
            i += 2
            continue
        if a in _DROP_FLAGS or (a.startswith("-o") and len(a) > 2):
            i += 1
            continue
        if a.startswith(_UNSAFE_PREFIXES):
            unsafe += 1
            i += 1
            continue
        if a == "-Xclang" and nxt is not None and (nxt in _UNSAFE_XCLANG or nxt.startswith(_UNSAFE_PREFIXES)):
            unsafe += 1
            # "-Xclang -load -Xclang lib.so" is four tokens; drop the value too when it is there
            has_value = nxt in _UNSAFE_XCLANG and i + 2 < len(rest) and rest[i + 2] == "-Xclang"
            i += 4 if has_value else 2
            continue
        if not a.startswith("-") and os.path.realpath(os.path.join(directory, a)) == src_real:
            i += 1
            continue
        out.append(a)
        i += 1
    return out, unsafe


def _entry_args(entry: dict) -> list[str]:
    if "arguments" in entry:
        return list(entry["arguments"])
    return shlex.split(entry.get("command", ""))


def load_compdb(path: Path, root: Path, excludes: list[str]) -> FlagsReport:
    entries = json.loads(path.read_text(encoding="utf-8"))
    units: dict[str, CompileUnit] = {}
    outside = unsafe_total = 0
    for entry in entries:
        directory = entry.get("directory", str(path.parent))
        src = os.path.realpath(os.path.join(directory, entry["file"]))
        rel = rel_to(src, root)
        if rel is None:
            outside += 1
            continue
        if is_excluded(rel, excludes) or Path(src).suffix.lower() not in SOURCE_EXTS:
            continue
        if src in units:
            units[src].configs += 1  # same file built more than once; we parse the first build
            continue
        args, unsafe = clean_args(_entry_args(entry), entry["file"], directory)
        unsafe_total += unsafe
        units[src] = CompileUnit(src, directory, args)
    return FlagsReport(
        source="compile_commands.json",
        path=rel_to(path, root) or str(path),
        units=sorted(units.values(), key=lambda u: u.file),
        skipped_outside_repo=outside,
        unsafe_flags_removed=unsafe_total,
    )


# ---------------------------------------------------------------- guessing


def guess_units(root: Path, excludes: list[str]) -> FlagsReport:
    """No database: parse C as C and C++ as C++, with -I for the repo root, include/ and the file's folder."""
    units = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in ALWAYS_SKIP_DIRS and d not in GUESS_SKIP_DIRS)
        for name in sorted(files):
            ext = Path(name).suffix.lower()
            if ext not in SOURCE_EXTS:
                continue
            full = os.path.realpath(os.path.join(dirpath, name))
            rel = rel_to(full, root)
            if rel is None or is_excluded(rel, excludes):
                continue
            lang = ["-x", "c", "-std=gnu11"] if ext in C_EXTS else ["-x", "c++", "-std=gnu++17"]
            incs = [f"-I{root}", f"-I{dirpath}"]
            if (root / "include").is_dir():
                incs.append(f"-I{root / 'include'}")
            units.append(CompileUnit(full, str(root), [*lang, *incs]))
    return FlagsReport(source="guessed", path=None, units=sorted(units, key=lambda u: u.file))


def find_units(root: Path, build_dir: Path | None, excludes: list[str]) -> FlagsReport:
    db = find_compdb(root, build_dir)
    if db is not None:
        return load_compdb(db, root, excludes)
    return guess_units(root, excludes)


# ---------------------------------------------------------------- system headers


_system_args: dict[str, list[str]] = {}


def parse_search_list(text: str) -> list[str]:
    """Turn the output of `<cc> -E -v -` into -isystem / -iframework args."""
    args: list[str] = []
    inside = False
    for line in text.splitlines():
        if "#include <...> search starts here:" in line:
            inside = True
            continue
        if "End of search list." in line:
            break
        p = line.strip()
        if not inside or not p:
            continue
        if p.endswith("(framework directory)"):
            args.append("-iframework" + p[: -len("(framework directory)")].strip())
        else:
            args.append("-isystem" + p)
    return args


def unit_language(unit: CompileUnit) -> str:
    """ "c" or "c++", from an explicit -x flag or else the file extension."""
    for i, a in enumerate(unit.args):
        if a == "-x" and i + 1 < len(unit.args):
            return "c" if unit.args[i + 1] == "c" else "c++"
        if a.startswith("-x") and len(a) > 2:
            return "c" if a[2:] == "c" else "c++"
    return "c" if Path(unit.file).suffix.lower() in C_EXTS else "c++"


def system_include_args(lang: str) -> list[str]:
    """Ask the system compiler where its headers live (for "c" or "c++"), so libclang finds them."""
    if lang in _system_args:
        return _system_args[lang]
    compilers = ("clang", "gcc", "cc") if lang == "c" else ("clang++", "g++", "c++")
    args: list[str] = []
    for cc in compilers:
        if shutil.which(cc) is None:
            continue
        try:
            r = subprocess.run(
                [cc, f"-x{lang}", "-E", "-v", "-"], input="", capture_output=True, text=True, timeout=60
            )
        except (OSError, subprocess.SubprocessError):
            continue
        args = parse_search_list(r.stderr)
        if args:
            break
    _system_args[lang] = args
    return args
