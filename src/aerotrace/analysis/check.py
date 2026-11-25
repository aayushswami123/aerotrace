"""What a diff can break, with data effects: `aerotrace check`.

On top of `diff` (changed, new and removed functions, their callers, data coupling), this compares an
outline of the old and the new version of every changed file:

- a function whose signature changed (parameters added, removed, reordered or retyped, return type,
  `static`, prototype): its call sites, how results are used, where it is named as a value, pointer calls
  (`signature.py`), judged against the NEW signature;
- a struct field or global that was retyped, removed, renamed or added, or whose initial value changed:
  its users (`data.py`);
- new compile errors in a changed file (a prototype that no longer matches its definition).

The two versions are: git mode, the base commit (git show) and the files on disk (the index); patch mode,
the files on disk (the index) and the patched text, parsed in memory. Nothing is written.

"breaks" means "will not build or link" and nothing else. The result is turned into the versioned
`aerotrace-check` JSON (`to_contract`); renderers in `aerotrace.ui` read only that.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from aerotrace.analysis import certainty, data, diff, graph, signature, symbols
from aerotrace.analysis.certainty import EXACT, PROBABLE, UNKNOWN
from aerotrace.analysis.diff import DiffImpact
from aerotrace.analysis.symbols import Fn
from aerotrace.index.history import Outline, Outliner
from aerotrace.toolchain.unidiff import FileDiff

FORMAT = "aerotrace-check"
VERSION = 1
NODE_CAP = 5000
BREAKS_ID = "{}#breaks"  # id of the break of a changed node that does not build (#49)
STATUSES = ("changed", "new", "removed", "breaks", "affected", "unknown", "no_effect")
USED_RESULT = {"stored", "compared", "checked", "passed", "returned", "used", "called"}
CXX_EXTS = {".cc", ".cpp", ".cxx", ".c++", ".hpp", ".hh", ".hxx", ".h++", ".ipp", ".tcc"}
# Errors only in newer compilers (clang 16+, GCC 14+); older ones warn and build. A break, but probable.
COMPILER_DEPENDENT = (
    "incompatible integer to pointer conversion",
    "incompatible pointer to integer conversion",
    "incompatible function pointer types",
    "incompatible pointer types",  # a warning in clang, an error in GCC 14+
    "implicit declaration of function",
    "call to undeclared function",
)
NEWER_COMPILERS = "an error in newer compilers (clang 16+, GCC 14+); older ones, or clang for some, only warn"
USER_FILES_COMPILED = 8  # files of users of a retyped field or global compiled to find what breaks
IFDEF_NOTE = "Uses in code that is not compiled with these flags (other #if branches) are not checked."
LINK_LEVEL = {
    "exact": EXACT,
    "calls_removed": EXACT,
    "uses_changed": EXACT,
    "virtual": PROBABLE,
    "ambiguous": PROBABLE,
    "via_pointer": PROBABLE,
    "data": PROBABLE,
}


# ---------------------------------------------------------------- results


@dataclass
class SigChange:
    """A function whose signature differs between the old and the new version."""

    fn: Fn  # in the index
    old: dict  # outline entries (see parse.function_outline)
    new: dict
    kinds: list[str]  # params_added, params_removed, reordered, retyped, returns, variadic, static, ...
    words: str  # what changed, in plain English
    same_underlying: bool  # only spellings changed; the types are the same on this build


@dataclass
class DeclChange:
    """A field or global whose declaration differs."""

    kind: str  # retyped | text | initial_value | removed | added
    old: dict | None
    new: dict | None
    var: data.Var | None  # in the index (None for things the index does not hold)
    file: str
    words: str
    hint: str = ""  # "maybe renamed to X"
    same_underlying: bool = False


@dataclass
class Node:
    id: str
    name: str
    kind: str
    file: str
    line: int
    status: str
    certainty: str
    link: str | None
    why: str
    distance: int
    via: str | None = None
    via_name: str | None = None
    lines: list[int] = field(default_factory=list)
    edge: str = "calls"  # kind of the edge to `via`: calls | reads | writes
    others: int = 0  # other links found to the same node


@dataclass
class CheckResult:
    diff: DiffImpact
    signatures: list[SigChange] = field(default_factory=list)
    decls: list[DeclChange] = field(default_factory=list)
    nodes: dict[str, Node] = field(default_factory=dict)
    blind_spots: list[tuple[str, str, str | None]] = field(default_factory=list)  # (text, scope, node)
    stopped_early: bool = False
    seconds_parsing: float = 0.0
    parses: int = 0


# ---------------------------------------------------------------- comparing two outlines


def _empty() -> Outline:
    return Outline([], [], [], 0)


def _pairs(old: list[dict], new: list[dict]) -> tuple[list[tuple[dict, dict]], list[dict], list[dict]]:
    """Match functions of two outlines: same clang ID, else the only one with that qualified name (a C++
    overload whose parameter types changed has a new ID). Returns (pairs, only old, only new)."""
    by_usr: dict[str, list[dict]] = {}
    for g in new:
        by_usr.setdefault(g["usr"], []).append(g)  # a prototype may appear several times: pair in order
    pairs, left = [], []
    used: set[int] = set()
    for f in old:
        same = by_usr.get(f["usr"])
        if same:
            g = same.pop(0)
            pairs.append((f, g))
            used.add(id(g))
        else:
            left.append(f)
    right = [g for g in new if id(g) not in used]
    old_names = Counter(f["qualified_name"] for f in left)
    new_names = Counter(g["qualified_name"] for g in right)
    still_left = []
    for f in left:
        q = f["qualified_name"]
        if old_names[q] == 1 and new_names[q] == 1:
            g = next(x for x in right if x["qualified_name"] == q)
            pairs.append((f, g))
            right.remove(g)
        else:
            still_left.append(f)
    return pairs, still_left, right


def _squash(sig: str) -> str:
    return "".join(sig.split())


def compare_signatures(old: dict, new: dict) -> tuple[list[str], str, bool]:
    """(kinds of change, plain words, only spellings changed). Empty kinds: the signature is the same."""
    kinds: list[str] = []
    words: list[str] = []
    op, np_ = old["params"], new["params"]
    if len(op) != len(np_):
        kinds.append("params_added" if len(np_) > len(op) else "params_removed")
        words.append(f"now takes {_n(len(np_), 'parameter')} (was {len(op)})")
    else:
        otypes, ntypes = [p["type"] for p in op], [p["type"] for p in np_]
        onames, nnames = [p["name"] for p in op], [p["name"] for p in np_]
        if (
            (otypes != ntypes or onames != nnames)
            and sorted(zip(onames, otypes, strict=False)) == sorted(zip(nnames, ntypes, strict=False))
            and all(onames)
        ):
            if onames != nnames:
                kinds.append("reordered")
                words.append(f"parameters reordered: ({', '.join(onames)}) -> ({', '.join(nnames)})")
        elif otypes != ntypes:
            kinds.append("retyped")
            for i, (a, b) in enumerate(zip(otypes, ntypes, strict=False), 1):
                if a != b:
                    words.append(f"parameter {i} is now '{b}' (was '{a}')")
        if [p["has_default"] for p in op] != [p["has_default"] for p in np_]:
            kinds.append("defaults")
            words.append("default arguments changed")
    if old["returns"] != new["returns"]:
        kinds.append("returns")
        words.append(f"returns '{new['returns']}' (was '{old['returns']}')")
    if old["variadic"] != new["variadic"]:
        kinds.append("variadic")
        words.append("is now variadic (...)" if new["variadic"] else "no longer takes '...'")
    if old["static"] != new["static"]:
        kinds.append("static")
        words.append("is now static (this file only)" if new["static"] else "is no longer static")
    if old["has_prototype"] != new["has_prototype"]:
        kinds.append("prototype")
        words.append("now has a prototype" if new["has_prototype"] else "no longer has a prototype")
    same = bool(kinds) and set(kinds) <= {"retyped", "returns"}
    if same:
        same = old["returns_canonical"] == new["returns_canonical"] and [p["canonical"] for p in op] == [
            p["canonical"] for p in np_
        ]
    return kinds, "; ".join(words), same


def _n(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _record_order(outline: Outline) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for v in sorted(outline.variables, key=lambda v: (v["container"] or "", v["position"], v["line"])):
        if v["kind"] == "field":
            out.setdefault(v["container"] or "", []).append(v["name"])
    return out


def _var_key(v: dict) -> tuple:
    return (v["kind"], v["container"] or "", v["name"])


# ---------------------------------------------------------------- the analysis


class _Run:
    def __init__(self, conn: sqlite3.Connection, root: Path, res: CheckResult, mode: str):
        self.conn = conn
        self.root = root
        self.res = res
        self.mode = mode
        self.lookup = diff._Collect(conn, root, DiffImpact(mode, None, 0))
        self.edited = {c.fn.id for c in res.diff.changed}
        self.index_limits = set(graph.index_limits(conn))
        # shown as the coverage line (proof), not as a limit
        self.outside_note = graph.outside_build_note(
            dict(conn.execute("SELECT key, value FROM meta").fetchall())
        )
        self.sig_seen: set[str] = set()
        self.new_defs: dict[
            str, list[str]
        ] = {}  # patch mode: extern functions the patch defines in new files
        # users of a field or global whose type really changed, by file, until their files are compiled
        self.retyped_users: dict[str, list[tuple]] = {}
        self.seen_broken: set[tuple[str, str, int]] = set()  # (removed name, file, line) listed (#57)
        self.compiled: set[str] = set()  # patch mode: files whose patched version was parsed
        self.no_proto: set[str] = set()  # functions declared somewhere (new side) without a prototype
        self.decl_handled: set[str] = (
            set()
        )  # qualified names of declarations whose users are listed  # functions whose signature change is already handled

    # -- nodes

    def add(self, node: Node) -> None:
        """Keep one node per id: the strongest status, then certainty, then the nearest."""
        cur = self.res.nodes.get(node.id)
        if cur is None:
            self.res.nodes[node.id] = node
            return

        def rank(n: Node) -> tuple:
            return (STATUSES.index(n.status), certainty.LEVELS.index(n.certainty), n.distance)

        if cur.distance == 0 and node.status == "breaks":
            # part of the change, and it will not build: it stays a changed node, and the break is kept as a
            # node of its own, so it is still counted (never hide it) (#49)
            cur.why = f"{cur.why}; but {node.why}"
            node.id = BREAKS_ID.format(node.id)
            node.via = None if node.via == cur.id else node.via
            self.add(node)
            return
        if node.distance == 0 and cur.status == "breaks":
            del self.res.nodes[cur.id]
            self.add(node)
            self.add(cur)
            return
        if rank(node) < rank(cur):
            node.others = cur.others + 1
            node.lines = sorted(set(node.lines) | set(cur.lines))
            self.res.nodes[node.id] = node
        else:
            cur.others += 1
            if node.file == cur.file:
                cur.lines = sorted(set(cur.lines) | set(node.lines))

    def fn_node(
        self,
        fn: Fn,
        status: str,
        level: str,
        link: str | None,
        why: str,
        distance: int,
        via: str | None,
        via_name: str | None,
        lines: list[int],
        edge: str = "calls",
    ) -> None:
        if status == "unknown":
            level = UNKNOWN
        elif level == UNKNOWN:
            status = "unknown"
        self.add(
            Node(
                fn.id,
                fn.qualified_name,
                "function",
                fn.file,
                fn.line_start,
                status,
                level,
                link,
                why,
                distance,
                via,
                via_name,
                sorted(set(lines)),
                edge,
            )
        )

    def defer(self, fn: Fn) -> bool:
        """Patch mode, a function the patch also edits, in a file whose patched version was parsed with the
        rest of the patch: whether its calls still match is known from the compiler (`errors`), not guessed
        from the old index."""
        return self.mode == "patch" and fn.id in self.edited and fn.file in self.compiled

    def blind(self, text: str, node: str | None = None) -> None:
        if text == self.outside_note:
            return
        scope = "index" if text in self.index_limits else "change"
        self.res.blind_spots.append((text, scope, node if scope == "change" else None))

    # -- the change itself

    def change_nodes(self) -> None:
        d = self.res.diff
        for c in d.changed:
            if c.state == "removed":
                key = f"removed:{c.fn.file}:{c.fn.line_start}:{c.fn.qualified_name}"
                self.add(Node(key, c.fn.qualified_name, "function", c.fn.file, c.fn.line_start, "removed",
                              EXACT, None, "removed by the patch", 0))  # fmt: skip
                self._removed_in_patch(c.fn, key)
            else:
                why = "new function" if c.state == "new" else f"changed ({_n(c.lines, 'line')})"
                self.add(Node(c.fn.id, c.fn.qualified_name, "function", c.fn.file, c.fn.line_start,
                              "new" if c.state == "new" else "changed", EXACT, None, why, 0))  # fmt: skip
        for r in d.removed:
            key = f"removed:{r.file}:{r.line_start}:{r.name}"
            self.add(Node(key, r.name, "function", r.file, r.line_start, "removed", EXACT, None,
                          "removed", 0))  # fmt: skip
            # the index no longer has it, so tables and pointers that name it are not linked any more
            self.blind(f"{r.name} was removed. A table or pointer in another file that still declares it "
                       "compiles, and only the link fails: those places are not listed (check --patch lists "
                       "them before the change).", key)  # fmt: skip
        for a in d.added:
            key = f"new:{a.file}:{a.line_start}:{a.name}"
            self.add(Node(key, a.name, "function", a.file, a.line_start, "new", EXACT, None,
                          "new function (in the patch; not in the index yet)", 0))  # fmt: skip
        for v in d.changed_vars:
            self.add(Node(v.id, v.name, v.kind, v.file, v.line, "changed", EXACT, None,
                          f"declaration changed; used by {_n(v.users, 'function')}", 0))  # fmt: skip

    def _removed_in_patch(self, fn: Fn, key: str) -> None:
        """Patch mode: the index still has the callers of a function the patch removes."""
        si = signature.signature_impact(self.conn, fn, depth=0)
        moved = self._moved_to(fn)
        for s in si.sites:
            level, why = s.certainty, f"calls {fn.name}, which this patch removes"
            new_file = self._moved_new(fn) if moved is None else None
            if new_file is not None:
                level = PROBABLE if level == EXACT else level
                why = f"calls {fn.name}, which this patch moves to {new_file}, a new file that is not in the build list yet"
            if new_file is not None:
                self.blind(f"{fn.name} moved to {new_file}, which is new and not in the build list yet, so "
                           "AeroTrace can't tell whether it will be built and linked", key)  # fmt: skip
            if moved is not None:
                self.fn_node(s.caller, "affected", PROBABLE, "calls_removed", f"calls {fn.name}, which this patch "
                             f"moves to {moved}", 1, key, fn.qualified_name, [s.line])  # fmt: skip
                continue
            if self.defer(s.caller):
                continue  # its patched code was compiled: a call that is left shows up as a new error
            if s.caller.id in self.edited:
                level, why = PROBABLE, why + " (the patch also edits this function; check it still calls it)"
            self.fn_node(s.caller, "breaks", level, "calls_removed", why, 1, key, fn.qualified_name, [s.line])
        for p in si.pointer_sites:
            self.fn_node(p.caller, "affected", PROBABLE, p.link, f"calls a pointer of the type of {fn.name}, "
                         "which this patch removes", 1, key, fn.qualified_name, [p.line])  # fmt: skip
        for a in si.address_sites:
            if a.in_function is not None:
                self.fn_node(a.in_function, "breaks", EXACT, "calls_removed",
                             f"names {fn.name} as a value ({a.how}), and this patch removes it", 1, key,
                             fn.qualified_name, [a.line])  # fmt: skip
            elif a.in_variable is not None:
                # a table that names it (a command table, a filter's callbacks): it no longer builds (static) or
                # no longer links (benchmark redis-05, curl-05)
                table = data.global_named(self.conn, a.in_variable, a.file)
                if table is None:
                    self.blind(f"{a.file}:{a.line}: {a.in_variable} names {fn.name}, which this patch removes; "
                               "it is not in the index, so it is not listed", key)  # fmt: skip
                    continue
                moved = self._moved_to(fn)
                if moved is not None:
                    self._var_node(table, "affected", PROBABLE, f"its initializer names {fn.name} ({a.file}:"
                                   f"{a.line}), which this patch moves to {moved}; it should still link", key,
                                   fn.qualified_name, [a.line])  # fmt: skip
                    continue
                level, why = self._removed_link(fn)
                self._var_node(table, "breaks", level, f"its initializer names {fn.name} ({a.file}:{a.line}); "
                               f"{why}", key, fn.qualified_name, [a.line])  # fmt: skip

    def _moved_new(self, fn: Fn) -> str | None:
        """The one new file of the patch that defines an extern function of this name (a real definition)."""
        found = self.new_defs.get(fn.qualified_name, [])
        if len(found) != 1 or fn.linkage == "internal":
            return None
        path, usr, sig = found[0]
        # the clang ID has the parameter types in C++; the signature text covers C
        return path if usr == fn.id and sig == _squash(fn.signature) else None

    def find_new_defs(self, outliner: Outliner) -> None:
        for path in sorted(self.res.diff.new_files):
            text = outliner.new_overlay.get(path)
            o = outliner.text(path, text) if text is not None else None
            for f in o.functions if o is not None else []:
                if not f.get("static") and "@aN" not in f["usr"]:  # not in an unnamed namespace
                    self.new_defs.setdefault(f["qualified_name"], []).append(
                        (path, f["usr"], _squash(f["signature"]))
                    )

    def proto_users(self, outliner: Outliner, pairs: list) -> None:
        """Patch mode: a prototype the patch removes. Callers in files the patch does not compile may have
        relied on it, so compile those files with the patch. A new error is a break; a file that can't be
        compiled leaves its callers as may-break. Nothing is flagged when the files still compile."""
        gone: dict[str, dict] = {}
        for _, _, o, n in pairs:
            if o is not None and n is not None:
                kept = {d["usr"] for d in n.declarations}
                gone.update({d["usr"]: d for d in o.declarations if d["usr"] not in kept})
        ids: dict[str, str] = {}  # function id -> name
        for d in gone.values():
            for fid in self.lookup._fns_declared(d):
                ids[fid] = d["qualified_name"]
        if not ids:
            return
        marks = ",".join("?" * len(ids))
        keys = sorted(ids)
        by_file: dict[str, list] = {}  # file -> [(function id, caller id or None, global or None, line)]
        for r in self.conn.execute(
            f"""SELECT e.dst, e.src, e.line, f.file FROM edges e JOIN functions f ON f.id = e.src
                WHERE e.kind = 'calls' AND e.dst IN ({marks}) ORDER BY e.dst, e.src, e.line""",
            keys,
        ):
            by_file.setdefault(r["file"], []).append((r["dst"], r["src"], None, r["line"]))
        # every other use of the name: its address taken, or named in a global's initializer
        for r in self.conn.execute(
            f"""SELECT function_id, in_function, in_variable, file, line FROM address_sites
                WHERE function_id IN ({marks}) ORDER BY function_id, file, line""",
            keys,
        ):
            held = data.global_named(self.conn, r["in_variable"], r["file"]) if r["in_variable"] else None
            by_file.setdefault(r["file"], []).append((r["function_id"], r["in_function"], held, r["line"]))
        by_file = {f: v for f, v in by_file.items() if f not in self.compiled}
        files = sorted(by_file)
        for i, f in enumerate(files):
            o, n = outliner.before_after(f, self.mode) if i < USER_FILES_COMPILED else (None, None)
            if o is not None and n is not None and not o.errors:
                self.errors(f, o, n)
                continue
            if i >= USER_FILES_COMPILED:
                unsure = f"{f} was not compiled: more than {USER_FILES_COMPILED} files use it"
            elif o is None or n is None:
                unsure = f"{f} could not be parsed with the change"
            else:
                unsure = (
                    f"{f} already had {_n(o.errors, 'error')} before the change, so a new one may be hidden"
                )
            fns = symbols.get_many(self.conn, sorted({c for _, c, g, _ in by_file[f] if c and g is None}))
            for fid, caller, held, line in by_file[f]:
                why = f"the prototype of {ids[fid]} was removed; {unsure}"
                if caller is None and held is None:
                    self.blind(f"{f}:{line} names {ids[fid]}, whose prototype was removed; {unsure}, and the "
                               "global that names it is not in the index")  # fmt: skip
                elif held is not None:
                    self._var_node(held, "breaks", PROBABLE, f"its initializer names {ids[fid]}; {why}", None,
                                   ids[fid], [line])  # fmt: skip
                elif caller in fns:
                    self.fn_node(
                        fns[caller], "breaks", PROBABLE, "calls_removed", why, 1, None, ids[fid], [line]
                    )
        if len(files) > USER_FILES_COMPILED:
            self.blind(f"{len(files) - USER_FILES_COMPILED} more files were not compiled with the change")
        shown = sorted(set(ids.values()) - set(self.res.diff.proto_removed))
        if shown and by_file:
            self.blind(diff.proto_note(shown))

    def _moved_to(self, fn: Fn) -> str | None:
        """The file where the patch defines a function of the same name again (it was moved), if any."""
        for a in self.res.diff.added:
            same_file = a.file == fn.file
            # a static copy elsewhere can't stand in for an extern one (and clashes with its prototype)
            if a.name in (fn.qualified_name, fn.name) and (
                same_file or (fn.linkage != "internal" and not a.static)
            ):
                return a.file
        return None

    def _removed_link(self, fn: Fn) -> tuple[str, str]:
        """How sure it is that a reference to the removed `fn` no longer builds or links, and why."""
        if fn.linkage == "internal":
            return EXACT, "it is static, so that does not compile"
        others = self.conn.execute(
            "SELECT COUNT(*) FROM functions WHERE qualified_name = ? AND id != ? AND linkage = 'external'",
            (fn.qualified_name, fn.id),
        ).fetchone()[0]
        if others == 0:
            return EXACT, "nothing else in the repo defines it, so the program does not link"
        return PROBABLE, f"{others} other definition(s) with that name exist; it may not link"

    # -- signatures

    def signatures(self, file: str, old: Outline, new: Outline) -> None:
        # definitions with definitions, prototypes with prototypes (they may be spelled differently)
        pairs = _pairs(old.functions, new.functions)[0] + _pairs(old.declarations, new.declarations)[0]
        seen = self.sig_seen
        for o, n in pairs:
            kinds, words, same = compare_signatures(o, n)
            index_side = n if self.mode == "git" else o
            if any(o is f for f in old.functions):
                fid = self.lookup._fn_in_file(index_side, file)
                ids = [fid] if fid else []
            else:
                ids = self.lookup._fns_declared(index_side)
            if not kinds:
                if o["head"] != n["head"]:
                    for fid in ids:
                        self.blind(
                            f"The head of {n['qualified_name']} ({file}:{n['line_start']}) changed, but its "
                            "signature looks the same (attributes, macros or qualifiers?). Check its callers "
                            "by hand.",
                            fid,
                        )
                continue
            for fid in ids:
                if fid in seen:
                    continue
                seen.add(fid)
                fn = symbols.get(self.conn, fid)
                if fn is None:
                    continue
                sc = SigChange(fn, o, n, kinds, words, same)
                self.res.signatures.append(sc)
                self._signature_effects(sc, new)

    def _signature_effects(self, sc: SigChange, new_outline: Outline) -> None:
        fn, new = sc.fn, sc.new
        node = self.res.nodes.get(fn.id)
        if node is not None and node.distance == 0:
            node.why = f"signature changed: {sc.words}"
        else:
            self.add(Node(fn.id, fn.qualified_name, "function", fn.file, fn.line_start, "changed", EXACT, None,
                          f"signature changed: {sc.words}", 0))  # fmt: skip
        si = signature.signature_impact(self.conn, fn, depth=2)
        self.res.stopped_early |= si.truncated
        has_proto = new["has_prototype"] and fn.qualified_name not in self.no_proto
        required = sum(1 for p in new["params"] if not p["has_default"])
        total = len(new["params"])
        cxx = Path(fn.file).suffix.lower() in CXX_EXTS
        link_fail = ""
        if cxx and "static" not in sc.kinds:
            protos = new_outline.protos.get(fn.qualified_name, [])
            if protos and new["signature"] not in protos:
                link_fail = (
                    f"; the prototype it is declared with ({protos[0]}) no longer matches, so it may not link"
                )
        for s in si.sites:
            broken = ""
            if self.mode == "git" and s.broken:
                broken = f"does not compile now: passes {_n(s.n_args, 'argument')}; {fn.name} {sc.words}"
            elif (
                self.mode == "patch"
                and has_proto
                and (s.n_args < required or (not new["variadic"] and s.n_args > total))
            ):
                want = str(total) if required == total else f"{required} to {total}"
                broken = f"passes {_n(s.n_args, 'argument')}; {fn.name} now takes {want}"
            elif new["returns"] == "void" and s.use in USED_RESULT:
                broken = (
                    f"uses its result ({signature.USE_WORDS.get(s.use, s.use)}); {fn.name} now returns void"
                )
            elif new["static"] and not sc.old["static"] and s.caller.file != fn.file:
                broken = f"is in another file; {fn.name} is now static, so the call will not link"
            elif link_fail:
                broken = f"calls {fn.name}{link_fail}"
            if broken:
                level = s.certainty
                if link_fail and not s.broken:
                    level = PROBABLE
                if self.defer(s.caller):
                    continue  # its patched code was compiled: a broken call shows up as a new error
                if self.mode == "patch" and s.caller.id in self.edited:
                    level = PROBABLE
                    broken += " (the patch also edits this function; check the call)"
                self.fn_node(s.caller, "breaks", level, s.link, broken, 1, fn.id, fn.qualified_name, [s.line])
                continue
            if not has_proto:
                why = f"calls {fn.name}, which is declared without a prototype: any call compiles; {sc.words}"
                level = PROBABLE
            elif sc.same_underlying:
                why = f"calls {fn.name}: {sc.words}, the same underlying type on this build; behaviour should not change"
                level = PROBABLE
            elif "reordered" in sc.kinds:
                why = f"calls {fn.name}: {sc.words}; this call must swap its arguments"
                level = s.certainty
            else:
                why = f"calls {fn.name}: {sc.words}"
                level = s.certainty
            self.fn_node(s.caller, "affected", level, s.link, why, 1, fn.id, fn.qualified_name, [s.line])
        for p in si.pointer_sites:
            self.fn_node(p.caller, "affected", PROBABLE, p.link,
                         f"calls a pointer of the old type of {fn.name} ({sc.words})", 1, fn.id,
                         fn.qualified_name, [p.line])  # fmt: skip
        for a in si.address_sites:
            # an incompatible function pointer type is an error in C++, and in C on clang 16+ and GCC 14+
            status, level = ("affected", PROBABLE) if sc.same_underlying else ("breaks", PROBABLE)
            why = f"names {fn.name} as a value ({a.how}); its pointer type there no longer matches " + (
                "(the same underlying types, so it should still build)"
                if sc.same_underlying
                else "(current compilers reject that: clang 16+, GCC 14+, every C++ compiler)"
            )
            if a.in_function is not None:
                self.fn_node(
                    a.in_function, status, level, "exact", why, 1, fn.id, fn.qualified_name, [a.line]
                )
            elif a.in_variable:
                row = self.conn.execute(
                    "SELECT id, kind, line FROM variables WHERE qualified_name = ? AND file = ? ORDER BY id",
                    (a.in_variable, a.file),
                ).fetchone()
                if row is not None:
                    self.add(Node(row["id"], a.in_variable, row["kind"], a.file, row["line"], status, level,
                                  "exact", why, 1, fn.id, fn.qualified_name, [a.line], "reads"))  # fmt: skip
        for h in si.upstream:
            self._hit(h, f"calls {{via}}, which calls {fn.name}")
        for b in si.blind_spots:
            if not b.startswith("Stopped at depth"):  # one depth line for the whole answer (the diff's)
                self.blind(b, fn.id)
        if si.truncated:
            self.blind(f"Signature effects follow callers {si.depth} steps further up; more exist.", fn.id)
        self.blind(IFDEF_NOTE)

    # -- declarations

    def declarations(self, file: str, index_file: str | None, old: Outline, new: Outline) -> None:
        okeys = {_var_key(v): v for v in old.variables}
        nkeys = {_var_key(v): v for v in new.variables}
        changes: list[DeclChange] = []
        same: set[str] = set()  # declarations whose text is the same on both sides
        for k in sorted(okeys.keys() & nkeys.keys()):
            o, n = okeys[k], nkeys[k]
            if o["type"] != n["type"]:
                same_type = o["canonical"] == n["canonical"]
                changes.append(DeclChange("retyped", o, n, None, file,
                                          f"type is now '{n['type']}' (was '{o['type']}')",
                                          same_underlying=same_type))  # fmt: skip
            elif o["decl"] != n["decl"]:
                changes.append(DeclChange("text", o, n, None, file,
                                          "its declaration text changed (bit-field width, attributes, array "
                                          "size or qualifiers?), but its type looks the same"))  # fmt: skip
            elif o["kind"] == "global" and o["init"] != n["init"]:
                changes.append(DeclChange("initial_value", o, n, None, file, "its initial value changed"))
            else:
                same.add(o["qualified_name"])
        gone = [okeys[k] for k in sorted(okeys.keys() - nkeys.keys())]
        came = [nkeys[k] for k in sorted(nkeys.keys() - okeys.keys())]
        for o in gone:
            hint = ""
            for n in came:
                if (n["kind"], n["container"], n["type"], n["position"]) == (
                    o["kind"], o["container"], o["type"], o["position"]
                ):  # fmt: skip
                    hint = f"maybe renamed to {n['name']}"
            changes.append(DeclChange("removed", o, None, None, file, "removed", hint))
        for n in came:
            hint = next(
                (
                    f"maybe renamed from {o['name']}"
                    for o in gone
                    if (n["kind"], n["container"], n["type"], n["position"])
                    == (o["kind"], o["container"], o["type"], o["position"])
                ),
                "",
            )
            changes.append(DeclChange("added", None, n, None, file, "added", hint))
        for c in changes:
            index_side = (c.new if self.mode == "git" else c.old) or {}
            if index_side:
                row = self.lookup._var(index_side["qualified_name"], index_file)
                if row is not None:
                    c.var = data.data_users(self.conn, row["id"], depth=0).target
            self.res.decls.append(c)
            self._decl_effects(c)
        # A changed line on a declaration that reads the same (a comment or spacing): no effect on users.
        for v in self.res.diff.changed_vars:
            if v.name in same and v.file == (index_file or file) and v.name not in self.decl_handled:
                self.decl_handled.add(v.name)
                node = self.res.nodes.get(v.id)
                if node is not None:
                    node.why = "only comments or spacing changed on its line"
        # field order or set changed: code that sets fields by position, or depends on the layout
        oorder, norder = _record_order(old), _record_order(new)
        for rec in sorted(set(oorder) & set(norder)):
            if oorder[rec] != norder[rec]:
                self._layout(rec, file, index_file, old if self.mode == "patch" else new)

    def _decl_node(self, c: DeclChange) -> str:
        v = c.old if c.kind == "removed" else c.new
        assert v is not None
        name = v["qualified_name"]
        if c.var is not None and c.kind != "removed":
            nid = c.var.id
            status = "new" if c.kind == "added" else "changed"
        elif c.kind == "removed":
            nid = (
                f"removed:{c.file}:{v['line']}:{name}"
                if c.var is None or self.mode == "git"
                else (f"removed:{c.var.file}:{c.var.line}:{name}")
            )
            status = "removed"
        else:
            nid = f"new:{c.file}:{v['line']}:{name}"
            status = "new"
        why = c.words + (f" ({c.hint})" if c.hint else "")
        node = self.res.nodes.get(nid)
        if node is not None and node.distance == 0:
            node.why = why
        else:
            self.add(Node(nid, name, v["kind"], c.var.file if c.var and status != "new" else c.file,
                          c.var.line if c.var and status != "new" else v["line"], status, EXACT, None, why, 0))  # fmt: skip
        if status == "removed" and c.var is not None and c.var.id in self.res.nodes:
            del self.res.nodes[c.var.id]  # the declaration is gone: one node for it, the removed one
        return nid

    def _decl_effects(self, c: DeclChange) -> None:
        nid = self._decl_node(c)
        v = c.old if c.kind == "removed" else c.new
        assert v is not None
        name = v["qualified_name"]
        du = None
        if c.kind == "removed":
            if self.mode == "patch" and c.var is not None:
                du = data.data_users(self.conn, c.var.id, depth=0)
                for u in du.uses:
                    level = u.certainty
                    why = f"uses {name}, which this change removes"
                    if self.defer(u.fn):
                        continue
                    if u.fn.id in self.edited:
                        level, why = PROBABLE, why + " (the patch also edits this function; check it)"
                    self.fn_node(u.fn, "breaks", level, "uses_changed", why, 1, nid, name, u.reads + u.writes,
                                 "writes" if u.writes else "reads")  # fmt: skip
                for e in du.escapes:
                    if e.in_variable is not None:
                        self._var_node(e.in_variable, "breaks", EXACT, f"its initializer takes the address of "
                                       f"{name}, which this change removes", nid, name, [e.line])  # fmt: skip
                        continue
                    self.fn_node(e.fn, "breaks", EXACT, "uses_changed", f"takes the address of {name}, which "
                                 "this change removes", 1, nid, name, [e.line], "reads")  # fmt: skip
                for i in du.initializers:
                    self._var_node(i.variable, "breaks", EXACT, f"its initializer sets {name}, which this "
                                   "change removes", nid, name, [i.line])  # fmt: skip
            else:
                # the index already has the removal: the places that no longer compile still use it
                query = f"{v['container'] or ''}::{v['name']}" if v["kind"] == "field" else v["name"]
                for gv in data.gone_fields(self.conn, query):
                    if gv.kind != v["kind"]:
                        continue
                    du = data.data_users_of(self.conn, gv, depth=0)
                    for b in du.broken:
                        key = (name, b.file, b.line)
                        if key in self.seen_broken:  # one name removed twice (extern + definition)
                            continue
                        self.seen_broken.add(key)
                        reason = data.compile_reason(b.reason)
                        if b.fn is not None:
                            self.fn_node(b.fn, "breaks", b.certainty, "uses_changed", reason, 1, nid, name,
                                         [b.line])  # fmt: skip
                            continue
                        # outside every function: a global's initializer that still uses it (#57)
                        hits = data.globals_at(self.conn, b.file, b.line)
                        sure = len(hits) == 1 and hits[0][1] == EXACT and b.certainty == EXACT
                        if not sure:  # nothing proves which global it is (or it may be no global at all)
                            self.blind(f"{b.file}:{b.line} {reason}", nid)
                        for gvar, level, where in hits:
                            level = EXACT if sure else PROBABLE
                            self._var_node(gvar, "breaks", level, f"{where} at {b.file}:{b.line} {reason}", nid,
                                           name, [b.line])  # fmt: skip
                if v["kind"] != "field":
                    self.blind(
                        f"{name} was removed. A file that still declares it (an extern left in a header) "
                        "compiles, and only the link fails: those uses are not listed.",
                        nid,
                    )
        elif c.kind == "added":
            if v["kind"] == "global" and v["usr"].startswith("c:@"):
                for r in self.conn.execute(
                    "SELECT id, qualified_name, kind, file, line, usr FROM variables WHERE qualified_name = ? "
                    "AND kind = 'global' ORDER BY id",
                    (name,),
                ):
                    if r["usr"].startswith("c:@") and r["file"] != c.file:
                        self.add(Node(r["id"], name, "global", r["file"], r["line"], "breaks", PROBABLE,
                                      "ambiguous", f"a new global with the same name is added in {c.file}; "
                                      "the program may not link (two definitions)", 1, nid, name))  # fmt: skip
        elif c.var is not None:
            self.decl_handled.add(c.var.qualified_name)
            du = data.data_users(self.conn, c.var.id, depth=0)
            for u in du.uses:
                lines = u.reads + u.writes
                edge = "writes" if u.writes else "reads"
                if c.kind == "retyped":
                    level, why = u.certainty, f"uses {name}: {c.words}"
                    if c.same_underlying:
                        level = PROBABLE
                        why += ", the same underlying type on this build; behaviour should not change"
                    elif u.fn.file not in self.compiled:
                        # a real type change: whether this use still builds is known only by compiling it
                        # with the change (benchmark redis-07, curl-07: int to pointer)
                        self.retyped_users.setdefault(u.fn.file, []).append(
                            ("fn", u.fn, level, why, nid, name, edge, u.reads + u.writes)
                        )
                        continue
                    self.fn_node(u.fn, "affected", level, "uses_changed", why, 1, nid, name, lines, edge)
                elif c.kind == "text":
                    self.fn_node(u.fn, "unknown", UNKNOWN, "uses_changed", f"uses {name}: {c.words}; AeroTrace "
                                 "can't tell what that does", 1, nid, name, lines, edge)  # fmt: skip
                elif c.kind == "initial_value" and u.reads:
                    self.fn_node(u.fn, "affected", PROBABLE, "data", f"reads {name}: {c.words}", 1, nid, name,
                                 u.reads, "reads")  # fmt: skip
            for i in du.initializers:
                why = f"its initializer sets {name}: {c.words}"
                if c.kind == "retyped" and not c.same_underlying and i.variable.file not in self.compiled:
                    # whether `{.x = 1.5}` still builds with the new type is known only by compiling it
                    self.retyped_users.setdefault(i.variable.file, []).append(
                        ("var", i.variable, EXACT, why, nid, name, "writes", [i.line])
                    )
                    continue
                self._var_node(i.variable, "affected", EXACT, why, nid, name, [i.line])
            for e in du.escapes:
                if e.in_variable is not None:
                    self._var_node(e.in_variable, "unknown", UNKNOWN, f"its initializer stores the address of "
                                   f"{name} ({e.detail}); code that uses it may change {name}", nid, name,
                                   [e.line])  # fmt: skip
                    continue
                self.fn_node(e.fn, "unknown", UNKNOWN, "uses_changed",
                             f"passes the address of {name} on ({e.detail}); what happens there is not known", 1,
                             nid, name, [e.line], "reads")  # fmt: skip
            if c.kind == "text":
                self.blind(f"{name}: {c.words}. Its users are listed as unknown.", nid)
        if du is not None:
            for b in du.blind_spots:
                self.blind(b, nid)
        if v["kind"] == "field":
            self.blind(IFDEF_NOTE)

    def _var_node(
        self, var: data.Var, status: str, level: str, why: str, via: str, via_name: str, lines: list[int]
    ) -> None:
        if level == UNKNOWN:
            status = "unknown"
        self.add(Node(var.id, var.shown, var.kind, var.file, var.line, status, level, "uses_changed", why, 1,
                      via, via_name, lines, "writes"))  # fmt: skip

    def _layout(self, record: str, file: str, index_file: str | None, index_outline: Outline) -> None:
        field_row = self.conn.execute(
            "SELECT id FROM variables WHERE kind = 'field' AND container = ? ORDER BY file = ? DESC, id",
            (record, index_file or ""),
        ).fetchone()
        text = (
            f"The fields of {record} changed order or number, so its size and layout changed. Code that sets "
            "its fields by position, or depends on its size (sizeof, memcpy, file or network formats), is not "
            "followed."
        )
        if field_row is None:
            self.blind(text)
            return
        du = data.data_users(self.conn, field_row["id"], depth=0)
        for g in du.by_position:
            self._var_node(g, "unknown", UNKNOWN, f"is a {record}; its initializer may set fields by position",
                           g.id, g.shown, [g.line])  # fmt: skip
            self.res.nodes[g.id].via = None
            self.res.nodes[g.id].via_name = record
        self.blind(text)

    # -- new compile errors

    def errors(self, file: str, old: Outline, new: Outline) -> None:
        before = Counter(d["text"] for d in old.diagnostics)
        for d in new.diagnostics:
            if before[d["text"]] > 0:
                before[d["text"]] -= 1
                continue
            f = next(
                (f for f in new.functions if f["line_start"] <= d["line"] <= f["line_end"]),
                None,
            )
            fid = self.lookup._fn_in_file(f, file) if f is not None else None
            fn = symbols.get(self.conn, fid) if fid else None
            text = f"{file}:{d['line']} does not compile after this change: {d['text']}"
            sure = EXACT
            if d["text"].startswith(COMPILER_DEPENDENT):
                sure, text = PROBABLE, f"{text} ({NEWER_COMPILERS})"
            why = text.split(" ", 1)[1]
            if fn is not None:
                self.fn_node(fn, "breaks", sure, "exact", why, 1, None, None, [d["line"]])
                continue
            # outside every function: the initializer of a global (a filter or command table, curl-05)
            holders = [
                g
                for v in new.variables
                if v["kind"] == "global" and v["line"] <= d["line"] <= v.get("line_end", v["line"])
                and (g := data.global_named(self.conn, v["qualified_name"], file)) is not None
            ]  # fmt: skip
            if len(holders) != 1:  # none, or several on that line: say so
                self.blind(text, fid if fid in self.res.nodes else None)
            for g in holders:
                level = sure if len(holders) == 1 else PROBABLE
                self._var_node(g, "breaks", level, f"its initializer {why}", None, None, [d["line"]])

    def compile_users(self, outliner: Outliner) -> None:
        """Compile the files of users of a retyped field or global (functions and initializers), before
        and after the change: a use with a new error breaks, the other uses are affected. A file not
        compiled (too many, or libclang could not load it), or one that already had errors before the
        change, leaves its uses unknown, never affected: AeroTrace can't say they still build."""
        files = sorted(self.retyped_users, key=lambda f: (-len(self.retyped_users[f]), f))
        for i, f in enumerate(files):
            o, n = outliner.before_after(f, self.mode) if i < USER_FILES_COMPILED else (None, None)
            if o is not None and n is not None:
                self.errors(f, o, n)
            if i >= USER_FILES_COMPILED:
                unsure = f"{f} was not compiled with the change (too many files)"
            elif o is None or n is None:
                unsure = f"{f} could not be parsed with the change"
            elif o.errors:
                unsure = (f"{f} already had {_n(o.errors, 'error')} before the change (a missing header?), so "
                          "a new one may be hidden")  # fmt: skip
            else:
                unsure = None
            for kind, obj, level, why, nid, name, edge, lines in self.retyped_users[f]:
                if unsure is not None:
                    status, level, why = (
                        "unknown",
                        UNKNOWN,
                        f"{why}; {unsure}: whether it still builds is not known",
                    )
                else:
                    status, why = "affected", f"{why}; it still compiles"
                if kind == "fn":
                    self.fn_node(obj, status, level, "uses_changed", why, 1, nid, name, lines, edge)
                else:
                    self._var_node(obj, status, level, why, nid, name, lines)
        if len(files) > USER_FILES_COMPILED:
            self.blind(f"Uses in {len(files) - USER_FILES_COMPILED} more files of a retyped field or global "
                       "were not compiled with the change; they are listed as unknown.")  # fmt: skip

    # -- callers found by the diff walk

    def walk_hits(self) -> None:
        d = self.res.diff
        for h in d.affected:
            cur = self.res.nodes.get(h.fn.id)
            if cur is not None and h.confidence != "calls_removed":
                cur.others += 1  # a signature or data effect above already says more about this one
                continue
            if h.distance > 1:
                self._hit(h, "calls {via}")  # further up: `via` is the closer caller, whatever the first link
            elif h.confidence == "calls_removed":
                self.fn_node(h.fn, "breaks", EXACT, h.confidence, f"still calls {h.via}, which this change "
                             "removes", h.distance, None, h.via, h.lines)  # fmt: skip
            elif h.confidence == "uses_changed" and h.via in self.decl_handled:
                continue  # the declaration's own effects above say more (and how sure)
            elif h.confidence == "uses_changed":
                self.fn_node(h.fn, "affected", EXACT, h.confidence, f"uses {h.via}, whose declaration changed",
                             h.distance, None, h.via, h.lines, "reads")  # fmt: skip
            elif h.confidence == "data":
                self.fn_node(h.fn, "affected", PROBABLE, h.confidence, f"reads data that {h.via} writes",
                             h.distance, None, h.via, h.lines, "reads")  # fmt: skip
            else:
                self._hit(h, "calls {via}")
        self.res.stopped_early |= d.truncated

    def _hit(self, h: graph.Hit, words: str) -> None:
        level = LINK_LEVEL.get(h.confidence, PROBABLE)
        why = words.format(via=h.via or "?")
        if level != EXACT:
            why += f" ({graph.CONFIDENCE_MEANING.get(h.confidence, h.confidence)})"
        self.fn_node(h.fn, "affected", level, h.confidence, why, h.distance, None, h.via, h.lines)


def _file_pairs(
    root: Path, diffs: list[FileDiff], res: DiffImpact, mode: str, outliner: Outliner, old: dict
) -> list[tuple[str, str | None, Outline | None, Outline | None]]:
    """(shown path, path in the index, old outline, new outline) for every code file that was mapped."""
    skip = set(res.not_indexed) | set(res.mismatched)
    out = []
    for fd in sorted(diffs, key=lambda d: (d.new_path or d.old_path or "", d.old_path or "")):
        if fd.binary:
            continue
        shown = fd.new_path or fd.old_path or ""
        if Path(shown).suffix.lower() not in diff.CODE_EXTS:
            continue
        index_path = fd.new_path if mode == "git" else fd.old_path
        if index_path in skip or (index_path is None and mode == "git" and shown in res.unreadable):
            continue
        if mode == "git":
            o = old.get(fd.old_path) if fd.old_path else _empty()
            n = outliner.now(fd.new_path) if fd.new_path else _empty()
        else:
            o = outliner.now(fd.old_path) if fd.old_path else _empty()
            if fd.new_path is None:
                n = _empty()
            else:
                text = diff.patched_text(root, fd)
                n = outliner.text(fd.new_path, text, fd.old_path or fd.new_path) if text is not None else None
        out.append((shown, index_path, o, n))
    return out


def run(
    conn: sqlite3.Connection,
    root: Path,
    diffs: list[FileDiff],
    *,
    mode: str,
    outliner: Outliner,
    base: str | None = None,
    depth: int = 5,
    old: dict[str, Outline | None] | None = None,
) -> CheckResult:
    """Everything `diff` finds, plus signature and data effects. `old`: git mode, outlines of the old
    versions (from `diff.load_git`)."""
    old = old or {}
    if mode == "patch":
        # parse each patched file with the other patched files (a .h and a .c changed together)
        for fd in diffs:
            if fd.new_path and not fd.binary and Path(fd.new_path).suffix.lower() in diff.CODE_EXTS:
                text = diff.patched_text(root, fd)
                if text is not None:
                    outliner.new_overlay[fd.new_path] = text
    d = diff.analyse(
        conn, root, diffs, mode=mode, base=base, depth=depth, old=old, now=outliner.now, patched=outliner.text
    )
    res = CheckResult(d)
    r = _Run(conn, root, res, mode)
    if mode == "patch":
        r.find_new_defs(outliner)
    r.change_nodes()
    pairs = _file_pairs(root, diffs, d, mode, outliner, old)
    for _, _, _, n in pairs:
        for f in (n.functions + n.declarations) if n is not None else []:
            if not f["has_prototype"]:
                r.no_proto.add(f["qualified_name"])
        r.no_proto.update(n.noproto if n is not None else [])
    if mode == "patch":
        r.compiled = {ip for _, ip, o, n in pairs if ip and o is not None and n is not None}
    for shown, index_path, o, n in pairs:
        if o is None or n is None:
            side = "old" if o is None else "new"
            r.blind(
                f"The {side} version of {shown} could not be parsed, so signature and declaration changes in it "
                "are not checked."
            )
            continue
        if index_path is None and mode == "patch":
            continue
        r.signatures(index_path or shown, o, n)
        r.declarations(shown, index_path, o, n)
        r.errors(index_path or shown, o, n)
    if mode == "patch":
        r.proto_users(outliner, pairs)
    r.compile_users(outliner)
    r.walk_hits()
    for b in d.blind_spots:
        r.blind(b)
    res.parses, res.seconds_parsing = outliner.parses, outliner.seconds
    _resolve_via(res)
    return res


def _resolve_via(res: CheckResult) -> None:
    """`via` of walk hits is a qualified name: map it to the node id when exactly one node has that name."""
    by_name: dict[str, list[str]] = {}
    for n in res.nodes.values():
        if not n.id.endswith(BREAKS_ID.format("")):  # the break of a changed node: point at the change
            by_name.setdefault(n.name, []).append(n.id)
    for n in res.nodes.values():
        if n.via is None and n.via_name:
            ids = by_name.get(n.via_name, [])
            if len(ids) == 1 and ids[0] != n.id:
                n.via = ids[0]
        elif n.via is not None and n.via not in res.nodes:
            n.via = None


# ---------------------------------------------------------------- the aerotrace-check JSON


def _node_rank(n: Node) -> tuple:
    return (STATUSES.index(n.status), certainty.LEVELS.index(n.certainty), n.distance, n.file, n.line, n.id)


def empty_contract(status: str, tool: str, message: str | None = None, change: dict | None = None) -> dict:
    """The JSON for runs that checked nothing: no_index, index_error, nothing_to_check."""
    return {"format": FORMAT, "version": VERSION, "tool": tool, "status": status, "message": message,
            "change": change}  # fmt: skip


def _change(d: DiffImpact, res: CheckResult) -> dict:
    states = Counter(c.state for c in d.changed)
    return {
        "mode": d.mode,
        "base": d.base if d.mode == "git" else None,
        "files": d.files,
        "other_files": d.ignored,
        "changed_functions": states["changed"],
        "new_functions": states["new"] + len(d.added),
        "removed_functions": states["removed"] + len(d.removed),
        "changed_declarations": sum(
            1 for n in res.nodes.values() if n.distance == 0 and n.kind != "function"
        ),
        "not_followed": {
            "outside": [
                {"file": o.file, "lines": o.lines, "other_side_lines": o.other_side} for o in d.outside
            ],
            "not_indexed": list(d.not_indexed),
            "deleted": list(d.deleted),
            "mismatched": list(d.mismatched),
            "unreadable": list(d.unreadable),
            "new_files": list(d.new_files),
        },
    }


OUTSIDE_EXAMPLES = 3  # repo sources outside the build named in every answer (the rest with --all)


def to_contract(
    conn: sqlite3.Connection, res: CheckResult, tool: str, cap: int = NODE_CAP, list_outside: bool = False
) -> dict:
    """The `aerotrace-check` v1 JSON (see aerotrace/ui/schema.py). Counts are over every node; the node
    list is capped at `cap`, lowest ranked dropped first. `list_outside` adds the sorted repo sources
    outside the build (proof.outside_build_files); by default only their count is given."""
    d = res.diff
    change = _change(d, res)
    if d.files == 0:
        return empty_contract("nothing_to_check", tool, None, change)
    nodes = sorted(res.nodes.values(), key=_node_rank)
    blind: dict[str, tuple[str, str | None]] = {}
    for text, scope, node in res.blind_spots:
        cur = blind.get(text)
        if cur is None or (scope == "change" and cur[0] == "index"):
            blind[text] = (scope, node)
    blind_list = [
        {"text": t, "scope": s, "node": n}
        for t, (s, n) in sorted(blind.items(), key=lambda kv: (kv[1][0], kv[0]))
    ]
    nf = change["not_followed"]
    gaps = sum(len(v) for v in nf.values()) + sum(1 for b in blind_list if b["scope"] == "change")
    count = Counter((n.status, n.certainty if n.status == "breaks" else "", n.distance > 1) for n in nodes)
    summary = {
        "breaks_exact": count[("breaks", EXACT, False)] + count[("breaks", EXACT, True)],
        "breaks_probable": count[("breaks", PROBABLE, False)] + count[("breaks", PROBABLE, True)],
        "affected_direct": count[("affected", "", False)],
        "affected_further": count[("affected", "", True)],
        "unknown": count[("unknown", "", False)] + count[("unknown", "", True)],
        "no_effect": 0,
        "not_followed": gaps,
    }
    keep = [n for n in nodes if n.distance == 0]
    rest = [n for n in nodes if n.distance != 0]
    room = max(0, cap - len(keep))
    kept = sorted(keep + rest[:room], key=_node_rank)
    omitted = len(nodes) - len(kept)
    ids = {n.id for n in kept}
    out_nodes, edges = [], []
    for n in kept:
        via = n.via if n.via in ids else None
        out_nodes.append(
            {
                "id": n.id,
                "name": n.name,
                "kind": n.kind,
                "file": n.file,
                "line": n.line,
                "status": n.status,
                "certainty": n.certainty,
                "link": n.link,
                "why": n.why + (f" (and {_n(n.others, 'other link')})" if n.others and n.distance else ""),
                "distance": n.distance,
                "via": via,
                "via_name": n.via_name,
                "lines": n.lines,
            }
        )
        if via is not None:
            edges.append({"from": n.id, "to": via, "kind": n.edge, "certainty": n.certainty})
    edges.sort(key=lambda e: (e["from"], e["to"], e["kind"]))
    blind_list = [{**b, "node": b["node"] if b["node"] in ids else None} for b in blind_list]
    files, functions = (
        conn.execute("SELECT COUNT(*) FROM files").fetchone()[0],
        conn.execute("SELECT COUNT(*) FROM functions").fetchone()[0],
    )
    cov = graph.build_coverage(conn)
    outside = cov.pop("outside_build")
    return {
        "format": FORMAT,
        "version": VERSION,
        "tool": tool,
        "status": "ok",
        "message": None,
        "change": change,
        "summary": summary,
        "nodes": out_nodes,
        "edges": edges,
        "sections": {
            # nodes of the change itself are listed under "What changed", not in a section
            "functions": [n["id"] for n in out_nodes if n["kind"] == "function" and n["distance"]],
            "data": [n["id"] for n in out_nodes if n["kind"] != "function" and n["distance"]],
            "tests_to_run": None,
            "untested_changes": None,
            "blind_spots": blind_list,
        },
        "omitted": {"nodes": omitted},
        "proof": {
            "index_files": files,
            "index_functions": functions,
            "depth": d.depth,
            "stopped_early": res.stopped_early,
            **cov,
            "outside_build": len(outside),
            "outside_build_examples": outside[:OUTSIDE_EXAMPLES],
            **({"outside_build_files": outside} if list_outside else {}),
        },
    }


def exit_code(contract: dict) -> int:
    """0 = nothing breaks, 1 = something breaks (exact or probable), 2 = could not check."""
    if contract["status"] in ("no_index", "index_error"):
        return 2
    s = contract.get("summary") or {}
    return 1 if s.get("breaks_exact") or s.get("breaks_probable") else 0


@dataclass
class Change:
    """A change read from git or from a patch, ready to check."""

    mode: str
    base: str | None
    diffs: list[FileDiff]
    old: dict[str, Outline | None]

    @property
    def want(self) -> list[str]:
        return diff.wanted(self.diffs, self.mode)


def load_change(
    root: Path, outliner: Outliner, *, base: str = "HEAD", patch_text: str | None = None
) -> Change:
    """Read the change: git mode (the working tree against `base`), or `patch_text` in patch mode.
    Raises ValueError or GitError when it can't be read."""
    if patch_text is not None:
        return Change("patch", None, diff.load_patch(root, patch_text), {})
    diffs, desc, old = diff.load_git(root, base, outliner, every=True)
    return Change("git", desc, diffs, old)


def _tool() -> str:
    from aerotrace import __version__

    return f"aerotrace {__version__}"


def check_loaded(
    conn: sqlite3.Connection,
    root: Path,
    change: Change,
    outliner: Outliner,
    depth: int = 5,
    list_outside: bool = False,
) -> dict:
    """The aerotrace-check JSON for a change, with an open index."""
    res = run(conn, root, change.diffs, mode=change.mode, outliner=outliner, base=change.base, depth=depth,
              old=change.old)  # fmt: skip
    return to_contract(conn, res, _tool(), list_outside=list_outside)


def check_change(
    root: Path,
    open_index,
    *,
    base: str = "HEAD",
    patch_text: str | None = None,
    build_dir: Path | None = None,
    depth: int = 5,
    list_outside: bool = False,
) -> tuple[dict, str]:
    """Read the change, open the index with `open_index(want) -> (conn, note)`, and return (the
    aerotrace-check JSON, note about the index). Raises ValueError or GitError when the change can't be
    read.

    Patch mode is for a change that is NOT applied yet (the hook checks a proposed edit before it is
    written): the index is built from the files on disk only, never from the patched text."""
    outliner = Outliner(root, build_dir)
    change = load_change(root, outliner, base=base, patch_text=patch_text)
    try:
        conn, note = open_index(change.want)
    except FileNotFoundError as e:
        msg = f"AeroTrace has no index for this repo and could not build one: {e}"
        return empty_contract("no_index", _tool(), msg), ""
    except Exception as e:
        return empty_contract("index_error", _tool(), f"The index could not be opened: {e}"), ""
    try:
        return check_loaded(conn, root, change, outliner, depth, list_outside), note
    finally:
        conn.close()
