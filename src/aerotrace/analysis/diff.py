"""Change impact for a diff: which functions and declarations a diff touches, and what that could break.

Two kinds of diff are supported, and the difference matters for line numbers:

- **git mode**: a commit compared with the working tree. The index describes the working tree, so the
  diff's *new* side lines up with the index. Removed lines belong to the *old* version, so we also parse
  the old version of each changed file (function boundaries only) to see which functions they came from.
  That is how a deleted function is found, even when git lines the deletion up with its neighbour.
- **patch mode**: a change that has not been applied yet. The index describes the files as they are now,
  so the patch's *old* side lines up with the index. We check that the patch really matches the files on
  disk; if it doesn't, the file is reported and left out instead of being mapped to the wrong lines.

A change outside every function (macros, types, includes, comments) can't be followed, so it is reported,
never dropped.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from aerotrace.analysis import graph, symbols
from aerotrace.analysis.graph import Hit
from aerotrace.analysis.symbols import Fn
from aerotrace.index import store
from aerotrace.index.history import Outline, Outliner
from aerotrace.toolchain import unidiff
from aerotrace.toolchain.compdb import SOURCE_EXTS
from aerotrace.toolchain.unidiff import FileDiff, parse_unified, whole_file_added

HEADER_EXTS = {".h", ".hh", ".hpp", ".hxx", ".h++", ".inl", ".ipp", ".tcc", ".inc"}
CODE_EXTS = SOURCE_EXTS | HEADER_EXTS

# ---------------------------------------------------------------- results


@dataclass
class ChangedFn:
    fn: Fn
    lines: int  # changed lines inside it (added and removed)
    state: str  # "changed" | "new" (every line added) | "removed" (a patch removes every line)


@dataclass
class RemovedFn:
    """A function the old version had and the working tree no longer has (git mode)."""

    name: str  # qualified name
    file: str
    line_start: int  # in the old version
    line_end: int
    still_called_by: list[Fn]  # functions in the working tree that still call a function of this name


@dataclass
class AddedFn:
    """A function only the patched version defines (patch mode): it is not in the index yet."""

    name: str  # qualified name
    file: str
    line_start: int  # in the patched version
    line_end: int
    static: bool = False  # internal linkage: it can't stand in for a removed function of another file


@dataclass
class ChangedVar:
    id: str
    name: str
    kind: str  # field | global | static_local
    file: str
    line: int
    users: int  # functions that read or write it


@dataclass
class Outside:
    """Changes in one file that are not inside any function or variable declaration."""

    file: str
    lines: list[int]  # changed lines, on the side the index describes
    other_side: list[int]  # lines on the other side (removed in git mode, added in patch mode)


@dataclass
class DiffImpact:
    mode: str  # "git" | "patch"
    base: str | None  # in git mode, a description of what we compared with
    depth: int
    files: int = 0  # code files the diff touches
    changed: list[ChangedFn] | None = None
    removed: list[RemovedFn] | None = None
    added: list[AddedFn] | None = None  # patch mode: functions the patch adds
    changed_vars: list[ChangedVar] | None = None
    affected: list[Hit] | None = None
    truncated: bool = False
    outside: list[Outside] | None = None
    not_indexed: list[str] | None = None  # code files changed but not in the index
    deleted: list[str] | None = None  # git mode: files deleted
    new_files: list[str] | None = None  # patch mode: files the patch creates (not analysed)
    mismatched: list[str] | None = None  # patch mode: the patch does not match the file on disk
    unreadable: list[str] | None = None  # git mode: the old version could not be read or parsed
    ignored: int = 0  # files that are not C or C++ (docs, build files, binaries)
    blind_spots: list[str] | None = None
    proto_removed: list[str] = field(default_factory=list)  # patch mode: prototype removed, definition kept

    def __post_init__(self) -> None:
        for name in (
            "changed", "removed", "added", "changed_vars", "affected", "outside", "not_indexed", "deleted",
            "new_files", "mismatched", "unreadable", "blind_spots",
        ):  # fmt: skip
            if getattr(self, name) is None:
                setattr(self, name, [])


# ---------------------------------------------------------------- helpers


def ranges(lines: list[int]) -> str:
    """[1,2,3,7,9,10] -> '1-3, 7, 9-10'."""
    out: list[list[int]] = []
    for n in sorted(set(lines)):
        if out and out[-1][1] == n - 1:
            out[-1][1] = n
        else:
            out.append([n, n])
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in out)


def _is_code(path: str, known: set[str]) -> bool:
    return path in known or Path(path).suffix.lower() in CODE_EXTS


def _matches_disk(root: Path, fd: FileDiff) -> bool:
    """Whether every old line a patch shows is exactly what the file holds now."""
    try:
        text = (root / str(fd.old_path)).read_bytes().decode("utf-8", "surrogateescape")
    except OSError:
        return False
    disk = text.split("\n")
    return all(
        0 < n <= len(disk) and disk[n - 1].rstrip("\r") == t.rstrip("\r") for n, t in fd.old_text.items()
    )


def _disk_lines(root: Path, path: str) -> dict[int, str]:
    try:
        text = (root / path).read_bytes().decode("utf-8", "surrogateescape")
    except OSError:
        return {}
    return {i: t for i, t in enumerate(text.split("\n"), 1)}


def patched_text(root: Path, fd: FileDiff) -> str | None:
    """Patch mode: the file as the patch would leave it (in memory; nothing is written)."""
    if fd.old_path is None:
        text = ""
    else:
        try:
            text = (root / fd.old_path).read_bytes().decode("utf-8", "surrogateescape")
        except OSError:
            return None
    return unidiff.apply(text, fd)


def patched_outline(root: Path, fd: FileDiff, patched: Callable[..., Outline | None]) -> Outline | None:
    text = patched_text(root, fd)
    if text is None or fd.new_path is None:
        return None
    return patched(fd.new_path, text, fd.old_path or fd.new_path)


def _still_calling(conn: sqlite3.Connection, name: str) -> list[str]:
    """Functions with a call to `name` that no longer has a definition."""
    rows = conn.execute(
        """SELECT DISTINCT function_id FROM blind_spots
           WHERE kind = 'unresolved_call' AND substr(detail, 1, ?) = ? ORDER BY function_id""",
        (len(name) + 1, name + ":"),
    )
    return [r[0] for r in rows]


# ---------------------------------------------------------------- the analysis


class _Collect:
    """Accumulates what one diff touches, file by file."""

    def __init__(self, conn: sqlite3.Connection, root: Path, res: DiffImpact):
        self.conn = conn
        self.root = root
        self.res = res
        # id -> [changed lines, all lines new, all lines removed, a line of its own body or head changed]
        self.changed: dict[str, list] = {}
        self.vars: dict[str, ChangedVar] = {}

    def mark(self, fid: str, n: int, new: bool = False, removed: bool = False) -> None:
        cur = self.changed.setdefault(fid, [0, True, True, False])
        cur[0] += n
        cur[1] = cur[1] and new
        cur[2] = cur[2] and removed
        cur[3] = True

    def mark_proto(self, fid: str, n: int) -> None:
        """A prototype line the patch removes. It is part of removing the function, so it must not turn
        "removed" into "changed"; on its own it only says the function changed."""
        self.changed.setdefault(fid, [0, True, True, False])[0] += n

    def mark_var(self, r: sqlite3.Row) -> None:
        if r["id"] not in self.vars:
            self.vars[r["id"]] = ChangedVar(r["id"], r["qualified_name"], r["kind"], r["file"], r["line"], 0)

    # -- matching outline items to the index

    def _fn_in_file(self, f: dict, file: str | None) -> str | None:
        """The indexed definition in `file` that an outline definition is: same clang ID, else same name."""
        if file is None:
            return None
        rows = self.conn.execute(
            "SELECT id, usr FROM functions WHERE file = ? AND qualified_name = ? ORDER BY id",
            (file, f["qualified_name"]),
        ).fetchall()
        same_usr = [r["id"] for r in rows if r["usr"] == f["usr"]]
        if len(same_usr) == 1:
            return same_usr[0]
        return rows[0]["id"] if len(rows) == 1 else None

    def _fns_declared(self, f: dict) -> list[str]:
        """Definitions a prototype declares: every one with its clang ID, else the only one with its name."""
        rows = self.conn.execute("SELECT id FROM functions WHERE usr = ? ORDER BY id", (f["usr"],)).fetchall()
        if rows:
            return [r["id"] for r in rows]
        rows = self.conn.execute(
            "SELECT id FROM functions WHERE qualified_name = ? ORDER BY id", (f["qualified_name"],)
        ).fetchall()
        return [rows[0]["id"]] if len(rows) == 1 else []

    def _var(self, qname: str, file: str | None) -> sqlite3.Row | None:
        cols = "SELECT id, qualified_name, kind, file, line FROM variables"
        if file is not None:
            rows = self.conn.execute(
                f"{cols} WHERE file = ? AND qualified_name = ? ORDER BY line, id", (file, qname)
            )
            row = rows.fetchone()
            if row is not None:
                return row
        rows = self.conn.execute(f"{cols} WHERE qualified_name = ? AND kind = 'global' ORDER BY id", (qname,))
        found = rows.fetchall()
        return found[0] if len(found) == 1 else None

    def explain(
        self,
        outline: Outline,
        lines: set[int],
        file: str,
        index_file: str | None,
        old: bool,
        patched: bool = False,
        removing: bool = False,
    ) -> set[int]:
        """Map lines onto an outline of one version of `file`; returns the lines that landed on something.

        `old` = the outline is of the old version (git mode), so a function that is not in the index any
        more was removed. `patched` = the outline is of the patched version (patch mode), so a function that
        is not in the index yet is new. `index_file` is the file's path in the index (None when it was
        deleted). `removing` = the lines are removed lines of the current file (patch mode)."""
        used: set[int] = set()
        for f in outline.functions:
            inside = {n for n in lines if f["line_start"] <= n <= f["line_end"]}
            if not inside:
                continue
            now = self._fn_in_file(f, index_file)
            if now is not None:
                self.mark(now, len(inside))
                used |= inside
            elif old:
                used |= inside
                callers = symbols.get_many(self.conn, _still_calling(self.conn, f["name"]))
                self.res.removed.append(
                    RemovedFn(
                        f["qualified_name"],
                        file,
                        f["line_start"],
                        f["line_end"],
                        sorted(callers.values(), key=lambda c: (c.file, c.line_start, c.id)),
                    )
                )
            elif patched:
                used |= inside  # a function the patch adds; listed by `added_in`
        for f in outline.declarations:
            inside = {n for n in lines if f["line_start"] <= n <= f["line_end"]}
            ids = self._fns_declared(f) if inside else []
            for fid in ids:
                if removing:
                    self.mark_proto(fid, len(inside))
                else:
                    self.mark(fid, len(inside))
            if ids:
                used |= inside
        for v in outline.variables:
            if v["line"] in lines:
                r = self._var(v["qualified_name"], index_file)
                if r is not None:
                    self.mark_var(r)
                    used.add(v["line"])
        return used

    # -- one file of the diff

    def map_file(
        self,
        path: str,
        fd: FileDiff,
        mode: str,
        old: Outline | None,
        now: Callable[[str], Outline | None] | None,
        new: Outline | None = None,
    ) -> None:
        """`new`: patch mode, an outline of the file with the patch applied (in memory)."""
        conn, res = self.conn, self.res
        if mode == "git":
            here = sorted({n for n, _ in fd.added})
            gaps = sorted({(g, o) for o, g in fd.removed})
            here_text, other_text = _disk_lines(self.root, path), fd.old_text
        else:
            here = sorted({o for o, _ in fd.removed})
            gaps = sorted({(g, n) for n, g in fd.added})
            here_text, other_text = fd.old_text, fd.new_text
        here_set = set(here)
        used_lines: set[int] = set()
        used_gaps: set[int] = set()
        for r in conn.execute(
            "SELECT id, line_start, line_end FROM functions WHERE file = ? ORDER BY line_start, id", (path,)
        ):
            s, e = r["line_start"], r["line_end"]
            inside = [n for n in here if s <= n <= e]
            touched = [g for g, _ in gaps if s <= g and g + 1 <= e]
            if not inside and not touched:
                continue
            used_lines.update(inside)
            used_gaps.update(touched)
            whole = not touched and all(k in here_set for k in range(s, e + 1))
            self.mark(r["id"], len(inside), new=whole and mode == "git", removed=whole and mode == "patch")
        for r in conn.execute(
            "SELECT id, qualified_name, kind, file, line FROM variables WHERE file = ? ORDER BY line, id",
            (path,),
        ):
            if r["line"] in here_set:
                used_lines.add(r["line"])
                self.mark_var(r)
        # Blank lines between functions can't change behaviour; leave them out of "could not follow".
        used_lines.update(n for n in here if not here_text.get(n, "x").strip())
        other = {o for g, o in gaps if g not in used_gaps and other_text.get(o, "x").strip()}

        # The index keeps one line per function and variable. Prototypes, `extern`s and fields declared
        # elsewhere need an outline of the current file.
        rest = {n for n in here if n not in used_lines}
        if rest and now is not None:
            outline_now = now(path)
            if outline_now is not None:
                used_lines |= self.explain(outline_now, rest, path, path, old=False, removing=mode == "patch")
        # Removed lines (git mode) belong to the old version: map them onto its outline.
        if mode == "git" and fd.removed:
            if old is None:
                res.unreadable.append(fd.old_path or path)
            else:
                removed_old = {o for o, _ in fd.removed}
                other -= self.explain(old, removed_old, fd.old_path or path, path, old=True)
        # Added lines (patch mode) belong to the patched version: map them onto its outline (#13).
        if mode == "patch" and new is not None and other:
            other -= self.explain(new, set(other), fd.new_path or path, path, old=False, patched=True)
        if mode == "patch" and new is not None:
            # A touched function the patched version no longer defines is removed, even when the patch
            # splits its deletion across a neighbour (#44) or only deletes lines (a shifted hunk).
            have = {f["usr"] for f in new.functions} | {f["qualified_name"] for f in new.functions}
            for r in conn.execute("SELECT id, usr, qualified_name FROM functions WHERE file = ?", (path,)):
                cur = self.changed.get(r["id"])
                if cur is not None and r["usr"] not in have and r["qualified_name"] not in have:
                    cur[1], cur[2] = False, True
            # A function only the patched version defines is new (#45).
            added_lines = {n for n, _ in fd.added}
            for f in new.functions:
                if self._fn_in_file(f, path) is None and any(
                    f["line_start"] <= n <= f["line_end"] for n in added_lines
                ):
                    res.added.append(
                        AddedFn(
                            f["qualified_name"],
                            fd.new_path or path,
                            f["line_start"],
                            f["line_end"],
                            bool(f.get("static")),
                        )
                    )
        out_lines = [n for n in here if n not in used_lines]
        if out_lines or other:
            res.outside.append(Outside(path, out_lines, sorted(other)))


def analyse(
    conn: sqlite3.Connection,
    root: Path,
    diffs: list[FileDiff],
    *,
    mode: str,
    base: str | None = None,
    depth: int = 5,
    old: dict[str, Outline | None] | None = None,
    now: Callable[[str], Outline | None] | None = None,
    patched: Callable[..., Outline | None] | None = None,
) -> DiffImpact:
    """Map a parsed diff onto the index and walk everything upstream of what changed.

    `old` holds outlines of the old versions of changed files (git mode), keyed by old path. `now` outlines
    a file as it is on disk; it is called only for changed lines the index can't place. `patched` (patch
    mode, `Outliner.text`) outlines a file with the patch applied in memory, so added lines are placed too."""
    if mode not in ("git", "patch"):
        raise ValueError(f"unknown mode {mode}")
    old = old or {}
    known = store.indexed_paths(conn)
    res = DiffImpact(mode, base, depth)
    col = _Collect(conn, root, res)

    for fd in sorted(diffs, key=lambda d: (d.new_path or d.old_path or "", d.old_path or "")):
        path = fd.new_path if mode == "git" else fd.old_path
        shown = fd.new_path or fd.old_path or ""
        if fd.binary or not _is_code(shown, known):
            res.ignored += 1
            continue
        res.files += 1
        if path is None:
            if mode == "git":
                res.deleted.append(shown)
                outline = old.get(shown)
                if outline is None:
                    res.unreadable.append(shown)
                else:
                    every = {n for f in outline.functions for n in range(f["line_start"], f["line_end"] + 1)}
                    col.explain(outline, every, shown, None, old=True)
            else:
                res.new_files.append(shown)
            continue
        if path not in known:
            res.not_indexed.append(path)
            continue
        if mode == "patch" and not _matches_disk(root, fd):
            res.mismatched.append(path)
            continue
        new = None
        if mode == "patch" and patched is not None and fd.new_path:
            new = patched_outline(root, fd, patched)
        col.map_file(path, fd, mode, old.get(fd.old_path or "") if mode == "git" else None, now, new)

    fns = symbols.get_many(conn, list(col.changed))
    res.changed = sorted(
        (
            ChangedFn(fns[i], n, "new" if new and real else "removed" if removed and real else "changed")
            for i, (n, new, removed, real) in col.changed.items()
            if i in fns
        ),
        key=lambda c: (c.fn.file, c.fn.line_start, c.fn.id),
    )
    res.proto_removed = sorted(
        fns[i].qualified_name for i, cur in col.changed.items() if i in fns and not cur[3] and cur[0]
    )
    res.removed.sort(key=lambda r: (r.file, r.line_start, r.name))
    res.added.sort(key=lambda a: (a.file, a.line_start, a.name))
    res.changed_vars = sorted(col.vars.values(), key=lambda v: (v.file, v.line, v.id))

    starts = [c.fn for c in res.changed]
    start_ids = {f.id for f in starts}
    seeds, labels = _seeds(conn, res, start_ids)
    res.affected, res.truncated = graph.walk_many(conn, starts, "callers", depth, seeds=seeds, labels=labels)
    res.blind_spots = _limits(conn, res)
    return res


def _seeds(
    conn: sqlite3.Connection, res: DiffImpact, start_ids: set[str]
) -> tuple[dict[str, tuple[str, str, list[int]]], dict[str, str]]:
    """Functions affected without a call to a changed function: they still call a removed function, use a
    changed declaration, or read data a changed function writes. Stronger links win."""
    seeds: dict[str, tuple[str, str, list[int]]] = {}
    labels: dict[str, str] = {}
    rank = graph.CONFIDENCE_ORDER.index

    def add(fid: str, conf: str, via: str, lines: list[int]) -> None:
        if fid in start_ids:
            return
        cur = seeds.get(fid)
        if cur is None or rank(conf) < rank(cur[0]):
            seeds[fid] = (conf, via, list(lines))
        elif cur[0] == conf and cur[1] == via:
            cur[2].extend(lines)

    for r in res.removed:
        key = f"removed:{r.file}:{r.line_start}:{r.name}"
        labels[key] = f"{r.name} (removed)"
        for c in r.still_called_by:
            lines = [
                row[0]
                for row in conn.execute(
                    """SELECT line FROM blind_spots WHERE function_id = ? AND kind = 'unresolved_call'
                       AND substr(detail, 1, ?) = ? ORDER BY line""",
                    (c.id, len(r.name.split("::")[-1]) + 1, r.name.split("::")[-1] + ":"),
                )
            ]
            add(c.id, "calls_removed", key, lines)
    for v in res.changed_vars:
        users = conn.execute(
            "SELECT src, line FROM edges WHERE dst = ? AND kind IN ('reads', 'writes') ORDER BY src, line",
            (v.id,),
        ).fetchall()
        v.users = len({u["src"] for u in users})
        labels[v.id] = v.name
        for u in users:
            add(u["src"], "uses_changed", v.id, [u["line"]])
    for c in res.changed:
        for base_id, base_name, callers in _lost_dispatch(conn, c.fn.id):
            key = f"overrode:{base_id}:{c.fn.id}"
            labels[key] = f"{base_name} (which {c.fn.qualified_name} may no longer override)"
            for caller, lines in callers:
                add(caller, "virtual", key, lines)
    for c in res.changed:
        _, readers = graph.data_coupling(conn, c.fn.id)
        for rid, (_, lines) in sorted(readers.items()):
            add(rid, "data", c.fn.id, lines)
    return seeds, labels


def _lost_dispatch(conn: sqlite3.Connection, fid: str) -> list[tuple[str, str, list[tuple[str, list[int]]]]]:
    """A changed method that overrides nothing (now), while a base class has a virtual method of the same
    name: the change (new parameters, say) may have stopped it overriding, so calls that dispatched to it
    through the base no longer do (#32). Returns [(base method id, its name, [(caller id, lines)])]; callers are
    probable, as we can't tell which of them reached this override before."""
    me = conn.execute("SELECT name, container FROM functions WHERE id = ?", (fid,)).fetchone()
    if me is None or not me["container"]:
        return []
    if conn.execute("SELECT 1 FROM edges WHERE src = ? AND kind = 'overrides'", (fid,)).fetchone():
        return []
    bases, todo = set(), [me["container"]]
    while todo:
        for (b,) in conn.execute("SELECT base FROM class_bases WHERE class = ? ORDER BY base", (todo.pop(),)):
            if b not in bases and len(bases) < 100:
                bases.add(b)
                todo.append(b)
    out = []
    for b in sorted(bases):
        for bid, bname in conn.execute(
            "SELECT id, qualified_name FROM functions WHERE container = ? AND name = ? AND is_virtual = 1 ORDER BY id",
            (b, me["name"]),
        ):
            callers: dict[str, list[int]] = {}
            for r in conn.execute(
                """SELECT src, line FROM edges WHERE dst = ? AND kind = 'calls' AND src != ?
                   ORDER BY src, line""",
                (bid, fid),
            ):
                callers.setdefault(r["src"], []).append(r["line"])
            out.append((bid, bname, sorted(callers.items())))
    return out


def proto_note(names: list[str]) -> str:
    shown = ", ".join(names[:5]) + (f" and {len(names) - 5} more" if len(names) > 5 else "")
    return (
        f"The prototype of {shown} is removed but the function is still defined. A file that relied on "
        "that header declaration may no longer compile (call to an undeclared function). In old C modes, or "
        "with other overloads, a call may instead resolve silently to an implicit declaration or another "
        "overload. AeroTrace does not follow which files include the header."
    )


def _limits(conn: sqlite3.Connection, res: DiffImpact) -> list[str]:
    notes: list[str] = []
    if res.outside:
        notes.append(
            "Some changed lines are outside every function (macros, types, includes, comments). "
            "AeroTrace does not follow who uses a macro or a type, so check those by hand."
        )
    if res.unreadable:
        notes.append(
            "The old version of some files could not be read, so a function removed there would not be listed."
        )
    if res.proto_removed:
        notes.append(proto_note(res.proto_removed))
    ids = [c.fn.id for c in res.changed]
    taken = 0
    for i in range(0, len(ids), 500):
        chunk = ids[i : i + 500]
        taken += conn.execute(
            f"SELECT COUNT(*) FROM address_taken WHERE function_id IN ({','.join('?' * len(chunk))})", chunk
        ).fetchone()[0]
    if taken:
        notes.append(
            f"{taken} changed function(s) are used as function pointers. Callers were matched by type; "
            "calls from outside this repo (callbacks into libraries) are not visible."
        )
    clashes = sum(1 for c in res.changed if c.fn.clash)
    if clashes:
        notes.append(
            f"{clashes} changed function(s) share a clang ID with another function; "
            "callers that could not tell them apart are marked 'ambiguous'."
        )
    notes += graph.template_notes(conn, [c.fn for c in res.changed] + [h.fn for h in res.affected])
    if res.truncated:
        notes.append(f"Stopped at depth {res.depth}; more callers exist further up.")
    return notes + graph.index_limits(conn)


# ---------------------------------------------------------------- loading a diff


def load_git(
    root: Path, ref: str = "HEAD", outliner: Outliner | None = None, every: bool = False
) -> tuple[list[FileDiff], str, dict[str, Outline | None]]:
    """The working tree compared with where HEAD left `ref`, including new files git does not track yet.
    Returns (file diffs, a short description of the base, outlines of the old versions of changed code
    files). Raises GitError."""
    from aerotrace.toolchain import git

    base = git.resolve_base(root, ref)
    diffs = parse_unified(git.diff_against(root, base.sha))
    for path in git.untracked(root):
        if Path(path).suffix.lower() not in CODE_EXTS:
            diffs.append(FileDiff(None, path, binary=True))  # counted as ignored
            continue
        try:
            text = (root / path).read_bytes().decode("utf-8", "surrogateescape")
        except OSError:
            continue
        n = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
        diffs.append(whole_file_added(path, n))
    short = base.sha[:12]
    if base.sha == base.ref_sha:
        desc = f"{ref} ({short})"
    else:
        desc = f"{short}, where this branch left {ref} ({ref} is now at {base.ref_sha[:12]})"
    old_paths = [
        fd.old_path
        for fd in diffs
        if fd.old_path
        and (fd.removed or every)
        and not fd.binary
        and Path(fd.old_path).suffix.lower() in CODE_EXTS
    ]
    outliner = outliner or Outliner(root)
    if every:
        # parse each old file with the old versions of the other changed files (its old headers)
        for rel in sorted(set(old_paths)):
            content = git.show(root, base.sha, rel)
            if content is not None:
                outliner.base_overlay[rel] = content
    return diffs, desc, {rel: outliner.at(base.sha, rel) for rel in sorted(set(old_paths))}


def load_patch(root: Path, text: str) -> list[FileDiff]:
    """A unified diff that has not been applied. Paths may be relative to the git top level or to `root`.
    Raises ValueError when the text is not a unified diff."""
    from aerotrace.toolchain import git

    diffs = parse_unified(text)
    if not diffs:
        raise ValueError("no file changes found; expected a unified diff (`git diff` or `diff -u` output)")
    pre = ""
    try:
        if git.is_repo(root):
            pre = git.prefix(root)
    except git.GitError:
        pre = ""
    if pre:
        for fd in diffs:
            fd.old_path = (
                fd.old_path[len(pre) :] if fd.old_path and fd.old_path.startswith(pre) else fd.old_path
            )
            fd.new_path = (
                fd.new_path[len(pre) :] if fd.new_path and fd.new_path.startswith(pre) else fd.new_path
            )
    return diffs


def wanted(diffs: list[FileDiff], mode: str) -> list[str]:
    """Code files the index should hold before mapping this diff."""
    out = {fd.new_path if mode == "git" else fd.old_path for fd in diffs if not fd.binary}
    return sorted(p for p in out if p and Path(p).suffix.lower() in CODE_EXTS)
