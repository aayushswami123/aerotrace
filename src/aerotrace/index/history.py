"""Outlines of single files: where each function, prototype, global and field sits.

The index describes the working tree only, and keeps one line per variable (its definition). Two questions
need more: which functions a diff *removed* lines from (the old version, read from git), and what a changed
line in a header declares (an `extern` or a prototype). For those we parse one file at a time with the same
compile flags, and store nothing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from aerotrace.frontends.cpp import parse as parse_mod
from aerotrace.toolchain import git, libclang
from aerotrace.toolchain.compdb import (
    C_EXTS,
    SOURCE_EXTS,
    CompileUnit,
    find_units,
    system_include_args,
    unit_language,
)


@dataclass
class Outline:
    functions: list[dict]  # definitions: {usr, name, qualified_name, line_start, line_end}
    declarations: list[dict]  # prototypes, same fields
    variables: list[dict]  # globals (extern too) and fields: {qualified_name, line}
    errors: int  # parse errors (the outline may be incomplete)


def _drop_language(args: list[str]) -> list[str]:
    out: list[str] = []
    skip = False
    for a in args:
        if skip:
            skip = False
            continue
        if a == "-x":
            skip = True
            continue
        if a.startswith("-x") and len(a) > 2:
            continue
        out.append(a)
    return out


def _args_for(
    rel: str, root: Path, units: dict[str, CompileUnit], fallback: CompileUnit | None
) -> tuple[list[str], str]:
    """(args, working folder) to parse `rel` with: its own compile entry, or for a header the flags of the
    first source file, parsed as a header."""
    u = units.get(rel)
    if u is not None:
        return u.args + system_include_args(unit_language(u)), u.directory
    ext = Path(rel).suffix.lower()
    if fallback is not None:
        lang = "c" if ext == ".h" and unit_language(fallback) == "c" else "c++"
        base, directory = _drop_language(fallback.args), fallback.directory
    else:
        lang = "c" if ext in C_EXTS or ext == ".h" else "c++"
        base, directory = [f"-I{root}", f"-I{root / 'include'}"], str(root)
    if ext in SOURCE_EXTS:
        return ["-x", "c" if ext in C_EXTS else "c++", *base, *system_include_args(lang)], directory
    return ["-x", f"{lang}-header", *base, *system_include_args(lang)], directory


class Outliner:
    """Parses one file at a time, with the flags the index used. Flags are loaded on first use."""

    def __init__(self, root: Path, build_dir: Path | None = None):
        self.root = root
        self.build_dir = build_dir
        self._units: dict[str, CompileUnit] | None = None
        self._fallback: CompileUnit | None = None

    def _parse(self, rel: str, content: str) -> Outline | None:
        if self._units is None:
            libclang.configure()
            flags = find_units(self.root, self.build_dir, [])
            self._units = {
                r: u for u in flags.units if (r := parse_mod.rel_to(u.file, self.root)) is not None
            }
            self._fallback = flags.units[0] if flags.units else None
        args, directory = _args_for(rel, self.root, self._units, self._fallback)
        got = parse_mod.function_outline(os.path.join(str(self.root), rel), content, args, directory)
        return Outline(**got) if got is not None else None

    def at(self, sha: str, rel: str) -> Outline | None:
        """`rel` as it was at commit `sha`; None when it did not exist there or could not be parsed."""
        content = git.show(self.root, sha, rel)
        return None if content is None else self._parse(rel, content)

    def now(self, rel: str) -> Outline | None:
        """`rel` as it is on disk now."""
        try:
            content = (self.root / rel).read_bytes().decode("utf-8", "surrogateescape")
        except OSError:
            return None
        return self._parse(rel, content)
