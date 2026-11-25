"""Outlines of single files: where each function, prototype, global and field sits.

The index describes the working tree only, and keeps one line per variable (its definition). Two questions
need more: which functions a diff *removed* lines from (the old version, read from git), and what a changed
line in a header declares (an `extern` or a prototype). For those we parse one file at a time with the same
compile flags, and store nothing.
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, field
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
    """See `parse.function_outline` for every key of the dicts."""

    functions: list[dict]  # definitions: {usr, name, qualified_name, line_start, line_end, signature, ...}
    declarations: list[dict]  # prototypes, same fields
    variables: list[dict]  # globals (extern too) and fields: {qualified_name, line, type, decl, init, ...}
    errors: int  # parse errors (the outline may be incomplete)
    diagnostics: list[dict] = field(default_factory=list)  # errors in this file: {line, text}
    protos: dict[str, list[str]] = field(default_factory=dict)  # prototypes in other files of the TU
    noproto: list[str] = field(default_factory=list)  # names declared there without a prototype


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
        self._cache: dict[tuple, Outline | None] = {}
        # Other files of the same change, so one version is parsed with its own headers: git mode, the
        # changed files as they were at the base; patch mode, the patched files (in memory).
        self.base_overlay: dict[str, str] = {}
        self.new_overlay: dict[str, str] = {}
        self.ref_lines: dict[str, frozenset[int]] = {}  # patch mode: changed lines (new side) by file
        self.parses = 0  # libclang parses done (the same text is parsed once)
        self.seconds = 0.0

    def _parse(
        self,
        rel: str,
        content: str,
        flags_from: str | None = None,
        overlay: dict[str, str] | None = None,
        ref_lines: frozenset[int] | None = None,
    ) -> Outline | None:
        others = sorted((r, c) for r, c in (overlay or {}).items() if r != rel)
        digest = hashlib.sha256()
        for r, c in others:
            digest.update(r.encode() + b"\0" + c.encode("utf-8", "surrogateescape") + b"\0")
        key = (
            rel,
            flags_from or rel,
            hashlib.sha256(content.encode("utf-8", "surrogateescape")).hexdigest(),
            digest.hexdigest(),
            tuple(sorted(ref_lines or ())),
        )
        if key in self._cache:
            return self._cache[key]
        if self._units is None:
            libclang.configure()
            flags = find_units(self.root, self.build_dir, [])
            self._units = {
                r: u for u in flags.units if (r := parse_mod.rel_to(u.file, self.root)) is not None
            }
            self._fallback = flags.units[0] if flags.units else None
        args, directory = _args_for(flags_from or rel, self.root, self._units, self._fallback)
        args = [*args, "-ferror-limit=0"]  # every new error counts: clang stops after 20 by default
        t = time.perf_counter()
        unsaved = [(os.path.join(str(self.root), r), c) for r, c in others]
        got = parse_mod.function_outline(
            os.path.join(str(self.root), rel), content, args, directory, unsaved, ref_lines
        )
        self.parses += 1
        self.seconds += time.perf_counter() - t
        out = Outline(**got) if got is not None else None
        self._cache[key] = out
        return out

    def at(self, sha: str, rel: str) -> Outline | None:
        """`rel` as it was at commit `sha`; None when it did not exist there or could not be parsed."""
        content = self.base_overlay.get(rel)
        if content is None:
            content = git.show(self.root, sha, rel)
        return None if content is None else self._parse(rel, content, overlay=self.base_overlay)

    def now(self, rel: str) -> Outline | None:
        """`rel` as it is on disk now."""
        try:
            content = (self.root / rel).read_bytes().decode("utf-8", "surrogateescape")
        except OSError:
            return None
        return self._parse(rel, content)

    def before_after(self, rel: str, mode: str) -> tuple[Outline | None, Outline | None]:
        """A file the change does not touch, parsed with the other files as they were before and after the
        change (git mode: the base versions of the changed files, then the files on disk; patch mode: the
        files on disk, then the patched ones). New errors in the second one are caused by the change."""
        try:
            content = (self.root / rel).read_bytes().decode("utf-8", "surrogateescape")
        except OSError:
            return None, None
        if mode == "git":
            return self._parse(rel, content, overlay=self.base_overlay), self._parse(rel, content)
        return self._parse(rel, content), self._parse(rel, content, overlay=self.new_overlay)

    def text(self, rel: str, content: str, flags_from: str | None = None) -> Outline | None:
        """`rel` with this content (a patched version in memory). `flags_from`: parse it with another
        file's compile flags (the old path of a renamed file). The functions that hold a changed line (see
        `ref_lines`) also list what they refer to."""
        return self._parse(rel, content, flags_from, self.new_overlay, self.ref_lines.get(rel))
