"""Parse one translation unit with libclang and turn it into plain facts.

The output is JSON-friendly (lists and dicts only) so it can be cached on disk and sent back from worker
processes. Nothing here decides IDs or resolves clashes; `aerotrace.index.merge` does that, because it
needs every translation unit at once.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
from pathlib import Path

from clang import cindex
from clang.cindex import AccessSpecifier, Cursor, StorageClass, TokenKind, TypeKind
from clang.cindex import CursorKind as K

from aerotrace.toolchain.compdb import CompileUnit, is_excluded, rel_to

FUNC_KINDS = {
    K.CXX_METHOD,
    K.FUNCTION_DECL,
    K.CONSTRUCTOR,
    K.DESTRUCTOR,
    K.FUNCTION_TEMPLATE,
    K.CONVERSION_FUNCTION,
}
METHOD_KINDS = {K.CXX_METHOD, K.CONSTRUCTOR, K.DESTRUCTOR, K.CONVERSION_FUNCTION}
CLASS_KINDS = {
    K.CLASS_DECL,
    K.STRUCT_DECL,
    K.UNION_DECL,
    K.CLASS_TEMPLATE,
    K.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION,
}
SCOPE_KINDS = CLASS_KINDS | {K.NAMESPACE}
DESCEND_KINDS = SCOPE_KINDS | {K.LINKAGE_SPEC, K.UNEXPOSED_DECL, K.FRIEND_DECL}
WRAPPER_KINDS = {K.UNEXPOSED_EXPR, K.PAREN_EXPR}
ARRAY_TYPES = {TypeKind.CONSTANTARRAY, TypeKind.INCOMPLETEARRAY, TypeKind.VARIABLEARRAY}
INDIRECT_TYPES = {TypeKind.POINTER, TypeKind.LVALUEREFERENCE, TypeKind.RVALUEREFERENCE}
REF_TYPES = {TypeKind.LVALUEREFERENCE, TypeKind.RVALUEREFERENCE}
STD_FUNCTION_CLASSES = {"function", "move_only_function", "function_ref"}
ASSIGN_OPS = {"=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>="}
OVERLOADED_WRITE_OPS = {f"operator{op}" for op in ASSIGN_OPS | {"++", "--"}}
MAX_ERRORS_KEPT = 10


# ---------------------------------------------------------------- libclang extras


def _overridden(cursor: Cursor) -> list[Cursor]:
    """The base-class methods this method overrides (clang_getOverriddenCursors)."""
    lib = cindex.conf.lib
    lib.clang_getOverriddenCursors.argtypes = [
        Cursor,
        ctypes.POINTER(ctypes.POINTER(Cursor)),
        ctypes.POINTER(ctypes.c_uint),
    ]
    lib.clang_getOverriddenCursors.restype = None
    lib.clang_disposeOverriddenCursors.argtypes = [ctypes.POINTER(Cursor)]
    lib.clang_disposeOverriddenCursors.restype = None
    arr = ctypes.POINTER(Cursor)()
    n = ctypes.c_uint(0)
    lib.clang_getOverriddenCursors(cursor, ctypes.byref(arr), ctypes.byref(n))
    out = []
    try:
        for i in range(n.value):
            c = Cursor()
            ctypes.memmove(ctypes.byref(c), ctypes.byref(arr[i]), ctypes.sizeof(Cursor))
            c._tu = cursor._tu
            out.append(c)
    finally:
        if n.value:
            lib.clang_disposeOverriddenCursors(arr)
    return out


def _template_of(cursor: Cursor) -> Cursor | None:
    try:
        t = cindex.conf.lib.clang_getSpecializedCursorTemplate(cursor)
    except Exception:
        return None
    if t is None or t.kind.is_invalid():
        return None
    return t


# libclang 17+ can tell us which operator an expression uses. Reading tokens instead is slow: inside a
# macro expansion the "tokens" of `x++` are the whole macro call (3.4M token reads for one Redis file).
BINARY_ASSIGN_KINDS = set(range(22, 33))  # CXBinaryOperator_Assign .. CXBinaryOperator_OrAssign
UNARY_INC_DEC_KINDS = {1, 2, 3, 4}  # CXUnaryOperator_PostInc, PostDec, PreInc, PreDec
UNARY_DEREF = 6  # CXUnaryOperator_Deref
_op_api: bool | None = None


def _has_operator_api() -> bool:
    global _op_api
    if _op_api is None:
        lib = cindex.conf.lib
        try:
            for name in ("clang_getCursorBinaryOperatorKind", "clang_getCursorUnaryOperatorKind"):
                fn = getattr(lib, name)
                fn.argtypes = [Cursor]
                fn.restype = ctypes.c_int
            _op_api = True
        except AttributeError:
            _op_api = False
    return _op_api


def binary_kind(c: Cursor) -> int | None:
    return cindex.conf.lib.clang_getCursorBinaryOperatorKind(c) if _has_operator_api() else None


def unary_kind(c: Cursor) -> int | None:
    return cindex.conf.lib.clang_getCursorUnaryOperatorKind(c) if _has_operator_api() else None


FUNCTION_TYPES = {TypeKind.FUNCTIONPROTO, TypeKind.FUNCTIONNOPROTO}


def function_type(t) -> str:
    """Canonical spelling of a function type, or of the function a pointer points to ('' otherwise)."""
    t = t.get_canonical()
    if t.kind == TypeKind.POINTER:
        t = t.get_pointee().get_canonical()
    return t.spelling if t.kind in FUNCTION_TYPES else ""


def file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _scope_parts(cursor: Cursor) -> list[str]:
    parts: list[str] = []
    p = cursor.semantic_parent
    while p is not None and p.kind != K.TRANSLATION_UNIT:
        if (p.kind in SCOPE_KINDS or p.kind in FUNC_KINDS) and p.spelling:
            parts.append(p.spelling)
        p = p.semantic_parent
    parts.reverse()
    return parts


def _enclosing_class(cursor: Cursor) -> str | None:
    p = cursor.semantic_parent
    while p is not None and p.kind != K.TRANSLATION_UNIT:
        if p.kind in CLASS_KINDS and p.spelling:
            return "::".join([*_scope_parts(p), p.spelling])
        p = p.semantic_parent
    return None


def _variable_kind(cursor: Cursor) -> str | None:
    """'global' (file / namespace / class static), 'static_local', or None for plain locals."""
    p = cursor.semantic_parent
    if p is None:
        return None
    if p.kind in SCOPE_KINDS or p.kind == K.TRANSLATION_UNIT:
        return "global"
    if p.kind in FUNC_KINDS and cursor.storage_class == StorageClass.STATIC:
        return "static_local"
    return None


def _strip_wrappers(stack: list[list], j: int) -> int:
    """Index of the first real parent above position j, skipping implicit casts and parentheses."""
    while j >= 0 and stack[j][0].kind in WRAPPER_KINDS:
        j -= 1
    return j


# ---------------------------------------------------------------- the collector


class Collector:
    """Walks one translation unit and collects facts about code inside the repo."""

    def __init__(self, root: Path, excludes: list[str]):
        self.root = root
        self.excludes = excludes
        self._rel: dict[str, str | None] = {}
        self.functions: dict[tuple[str, str], dict] = {}  # (usr, file) -> function record
        self.variables: dict[tuple[str, str], dict] = {}  # (usr, file) -> variable record
        self.class_bases: dict[str, list[str]] = {}
        self.calls: set[tuple] = set()  # (caller_usr, caller_file, callee_usr, line, dispatch, callee_name)
        self.accesses: set[tuple] = set()  # (fn_usr, fn_file, var_usr, "reads"/"writes", line)
        self.pointer_calls: set[tuple] = set()  # (fn_usr, fn_file, line, function type called or "")
        self.overrides: set[tuple] = set()  # (usr, base_usr)
        self.address_taken: dict[str, str] = {}  # function usr -> its type, for functions used as a value
        self.external_calls: dict[tuple[str, str], int] = {}

    # -- repo membership
    def rel(self, f) -> str | None:
        if f is None:
            return None
        name = f.name
        if name not in self._rel:
            r = rel_to(name, self.root)
            self._rel[name] = None if r is None or is_excluded(r, self.excludes) else r
        return self._rel[name]

    def cursor_rel(self, c: Cursor) -> str | None:
        return self.rel(c.location.file)

    # -- declarations
    def collect(self, tu_cursor: Cursor) -> None:
        for ch in tu_cursor.get_children():
            self.visit_decl(ch)

    def visit_decl(self, c: Cursor) -> None:
        rel = self.cursor_rel(c)
        if rel is None:
            return
        k = c.kind
        if k in FUNC_KINDS:
            if c.is_definition():
                self.record_function(c, rel)
            elif k == K.CXX_METHOD and c.is_pure_virtual_method():
                self.record_function(c, rel, pure=True)
        elif k == K.FIELD_DECL:
            self.record_variable(c, rel, "field")
        elif k == K.VAR_DECL:
            kind = _variable_kind(c)
            if kind == "global":
                self.record_variable(c, rel, kind)
                self.scan_initializer(c)
        elif k in DESCEND_KINDS:
            if k in CLASS_KINDS and c.is_definition():
                self.record_class(c)
            for ch in c.get_children():
                self.visit_decl(ch)

    def record_variable(self, c: Cursor, rel: str, kind: str) -> None:
        usr = c.get_usr()
        if not usr or not c.spelling:
            return
        key = (usr, rel)
        if key in self.variables and not (c.kind == K.VAR_DECL and c.storage_class != StorageClass.EXTERN):
            return
        self.variables[key] = {
            "usr": usr,
            "name": c.spelling,
            "qualified_name": "::".join([*_scope_parts(c), c.spelling]),
            "container": _enclosing_class(c),
            "kind": kind,
            "type": c.type.spelling,
            "file": rel,
            "line": c.location.line,
            # C's `int x;` (a tentative definition) is a definition too; only `extern` without a value is not.
            "is_definition": kind != "global" or c.is_definition() or c.storage_class != StorageClass.EXTERN,
        }

    def record_class(self, c: Cursor) -> None:
        if not c.spelling:
            return
        bases = []
        for ch in c.get_children():
            if ch.kind != K.CXX_BASE_SPECIFIER:
                continue
            decl = ch.type.get_declaration()
            if decl.kind.is_invalid() or not decl.spelling:
                bases.append(ch.type.spelling)
            else:
                bases.append("::".join([*_scope_parts(decl), decl.spelling]))
        name = "::".join([*_scope_parts(c), c.spelling])
        known = self.class_bases.setdefault(name, [])
        known.extend(b for b in bases if b not in known)

    def record_function(self, c: Cursor, rel: str, pure: bool = False) -> None:
        usr = c.get_usr()
        if not usr:
            return
        key = (usr, rel)
        existing = self.functions.get(key)
        if existing is not None and not (existing["is_pure_virtual"] and not pure):
            return
        sig = c.displayname
        if c.kind not in (K.CONSTRUCTOR, K.DESTRUCTOR, K.CONVERSION_FUNCTION):
            sig = f"{c.result_type.spelling} {sig}"
        if c.kind in (K.CXX_METHOD, K.CONVERSION_FUNCTION) and c.is_const_method():
            sig += " const"
        is_virtual = c.kind in (K.CXX_METHOD, K.DESTRUCTOR) and bool(c.is_virtual_method())
        public = c.access_specifier == AccessSpecifier.PUBLIC if c.kind in METHOD_KINDS else True
        self.functions[key] = {
            "usr": usr,
            "name": c.spelling,
            "qualified_name": "::".join([*_scope_parts(c), c.spelling]),
            "container": _enclosing_class(c),
            "file": rel,
            "line_start": c.extent.start.line,
            "line_end": c.extent.end.line,
            "signature": sig,
            "linkage": "internal" if c.linkage == cindex.LinkageKind.INTERNAL else "external",
            "is_virtual": is_virtual,
            "is_pure_virtual": pure,
            "is_public": public,
        }
        if is_virtual:
            for base in _overridden(c):
                if self.cursor_rel(base) is not None and base.get_usr():
                    self.overrides.add((usr, base.get_usr()))
        if not pure:
            self.walk_body(c, usr, rel)

    # -- bodies (explicit stack: the python bindings have no parent pointer, and recursion can overflow)
    def walk_body(self, fn: Cursor, usr: str, rel: str) -> None:
        stack: list[list] = [[fn, 0]]
        iters = [iter(list(fn.get_children()))]
        while iters:
            child = next(iters[-1], None)
            if child is None:
                iters.pop()
                stack.pop()
                continue
            stack[-1][1] += 1
            parent_idx = stack[-1][1] - 1  # index of `child` inside its parent
            self.visit_expr(child, stack, parent_idx, usr, rel)
            stack.append([child, 0])
            iters.append(iter(list(child.get_children())))

    def visit_expr(self, c: Cursor, stack: list[list], idx: int, usr: str, rel: str) -> None:
        k = c.kind
        if k == K.CALL_EXPR:
            self.on_call(c, usr, rel)
        elif k in (K.MEMBER_REF_EXPR, K.DECL_REF_EXPR):
            ref = c.referenced
            if ref is not None and ref.kind in FUNC_KINDS:
                if k == K.DECL_REF_EXPR and not self.is_callee(stack, idx):
                    self.mark_address_taken(ref)
                return
            self.on_ref(c, stack, idx, usr, rel)
        elif k == K.MEMBER_REF and len(stack) == 1:
            self.on_ref(c, stack, idx, usr, rel, force_write=True)  # constructor initializer: `: x_(0)`
        elif k == K.VAR_DECL and _variable_kind(c) == "static_local":
            self.record_variable(c, rel, "static_local")

    @staticmethod
    def is_callee(stack: list[list], idx: int) -> bool:
        """True when the DECL_REF_EXPR at child position `idx` of stack[-1] is the function being called."""
        j = len(stack) - 1
        pos = idx
        while j >= 0 and stack[j][0].kind in WRAPPER_KINDS:
            if pos != 0:
                return False
            pos = stack[j - 1][1] - 1 if j > 0 else 0
            j -= 1
        return j >= 0 and stack[j][0].kind == K.CALL_EXPR and pos == 0

    def mark_address_taken(self, ref: Cursor) -> None:
        if ref.spelling.startswith("operator"):
            return  # overloaded operator calls show up as a bare reference
        ref = self.canonical(ref)
        if ref.get_usr():
            self.address_taken.setdefault(ref.get_usr(), function_type(ref.type) or "")

    def scan_initializer(self, var: Cursor) -> None:
        """Functions named in a global's initializer (handler tables) have their address taken."""
        todo = list(var.get_children())
        while todo:
            c = todo.pop()
            if c.kind == K.DECL_REF_EXPR and c.referenced is not None and c.referenced.kind in FUNC_KINDS:
                self.mark_address_taken(c.referenced)
            todo.extend(c.get_children())

    def canonical(self, ref: Cursor) -> Cursor:
        for _ in range(5):
            t = _template_of(ref)
            if t is None:
                break
            ref = t
        return ref

    def on_call(self, c: Cursor, usr: str, rel: str) -> None:
        ref = c.referenced
        line = c.location.line
        if ref is None or ref.kind not in FUNC_KINDS or self.is_std_function(ref):
            # through a pointer, std::function or a dependent name: remember the type that is called
            self.pointer_calls.add((usr, rel, line, self.called_type(c)))
            return
        ref = self.canonical(ref)
        target = ref.get_usr()
        if not target:
            self.pointer_calls.add((usr, rel, line, ""))
            return
        if self.is_builtin(ref) or (self.cursor_rel(ref) is None and not self.might_be_defined_here(ref)):
            key = (usr, rel)
            self.external_calls[key] = self.external_calls.get(key, 0) + 1
            return
        dispatch = ref.kind in (K.CXX_METHOD, K.DESTRUCTOR) and ref.is_virtual_method() and self.dispatches(c)
        self.calls.add((usr, rel, target, line, dispatch, ref.spelling))

    def might_be_defined_here(self, ref: Cursor) -> bool:
        """A function declared in a system header can still be defined in the repo (own strlcpy)."""
        defn = ref.get_definition()
        return defn is not None and self.cursor_rel(defn) is not None

    @staticmethod
    def called_type(call: Cursor) -> str:
        """The function type a call through a pointer expects, e.g. 'void (struct client *)'."""
        callee = next(call.get_children(), None)
        while callee is not None and callee.kind in WRAPPER_KINDS and callee.type.kind == TypeKind.INVALID:
            callee = next(callee.get_children(), None)
        return function_type(callee.type) if callee is not None else ""

    @staticmethod
    def is_builtin(ref: Cursor) -> bool:
        """Compiler builtins (__builtin_expect, __atomic_*, __sync_*) are declared implicitly where first used."""
        return ref.spelling.startswith(("__builtin_", "__atomic_", "__sync_", "__c11_atomic_"))

    @staticmethod
    def is_std_function(ref: Cursor) -> bool:
        if ref.spelling != "operator()":
            return False
        p = ref.semantic_parent
        return p is not None and p.spelling in STD_FUNCTION_CLASSES

    def dispatches(self, call: Cursor) -> bool:
        """True when a virtual method is called through a pointer or reference (not `Base::f()`)."""
        callee = next(call.get_children(), None)
        if callee is None or callee.kind != K.MEMBER_REF_EXPR:
            return False
        toks = [t.spelling for t in callee.get_tokens()]
        if "::" in toks:
            return False
        ops = [t for t in toks if t in (".", "->")]
        if not ops or ops[-1] == "->":
            return True
        base = next(callee.get_children(), None)
        return base is not None and self.is_indirect(base)

    def is_indirect(self, e: Cursor) -> bool:
        while e.kind in WRAPPER_KINDS:
            e = next(e.get_children(), None)
            if e is None:
                return False
        if e.kind in (K.DECL_REF_EXPR, K.MEMBER_REF_EXPR):
            r = e.referenced
            return r is not None and r.type.get_canonical().kind in INDIRECT_TYPES
        if e.kind == K.CALL_EXPR:
            r = e.referenced
            return r is not None and r.result_type.get_canonical().kind in REF_TYPES
        if e.kind == K.CXX_THIS_EXPR:
            return True
        if e.kind == K.UNARY_OPERATOR:
            kind = unary_kind(e)
            return (
                kind == UNARY_DEREF
                if kind is not None
                else next((t.spelling for t in e.get_tokens()), "") == "*"
            )
        return False

    def on_ref(
        self, c: Cursor, stack: list[list], idx: int, usr: str, rel: str, force_write: bool = False
    ) -> None:
        ref = c.referenced
        if ref is None:
            return
        if ref.kind == K.FIELD_DECL:
            pass
        elif ref.kind == K.VAR_DECL and _variable_kind(ref) is not None:
            if _variable_kind(ref) == "static_local":
                self.record_variable(ref, self.cursor_rel(ref) or rel, "static_local")
        else:
            return
        if self.cursor_rel(ref) is None:
            return
        target = ref.get_usr()
        if not target:
            return
        write = force_write or self.is_write(stack, idx, ref)
        self.accesses.add((usr, rel, target, "writes" if write else "reads", c.location.line))

    # -- read or write?
    def is_write(self, stack: list[list], idx: int, ref: Cursor) -> bool:
        is_array = ref.type.get_canonical().kind in ARRAY_TYPES
        j = len(stack) - 1
        pos = idx
        while j >= 0:
            pc = stack[j][0]
            if pc.kind in WRAPPER_KINDS or (pc.kind == K.ARRAY_SUBSCRIPT_EXPR and is_array and pos == 0):
                pos = stack[j - 1][1] - 1 if j > 0 else 0
                j -= 1
            else:
                break
        if j < 0:
            return False
        pc = stack[j][0]
        if pc.kind == K.CALL_EXPR:
            # libclang lists the object an overloaded operator acts on first
            r = pc.referenced
            return pos == 0 and r is not None and r.spelling in OVERLOADED_WRITE_OPS
        if pos != 0:
            return False
        if pc.kind == K.COMPOUND_ASSIGNMENT_OPERATOR:
            return True
        if pc.kind == K.BINARY_OPERATOR:
            kind = binary_kind(pc)
            return kind in BINARY_ASSIGN_KINDS if kind is not None else self.binary_op(pc) in ASSIGN_OPS
        if pc.kind == K.UNARY_OPERATOR:
            kind = unary_kind(pc)
            if kind is not None:
                return kind in UNARY_INC_DEC_KINDS
            toks = [t.spelling for t in pc.get_tokens()]
            return bool(toks) and (toks[0] in ("++", "--") or toks[-1] in ("++", "--"))
        return False

    @staticmethod
    def binary_op(c: Cursor) -> str | None:
        lhs = next(c.get_children(), None)
        if lhs is None:
            return None
        end = lhs.extent.end.offset
        for t in c.get_tokens():
            if t.kind == TokenKind.PUNCTUATION and t.extent.start.offset >= end:
                return t.spelling
        return None


# ---------------------------------------------------------------- one translation unit

_index: cindex.Index | None = None


def _get_index() -> cindex.Index:
    global _index
    if _index is None:
        _index = cindex.Index.create()
    return _index


def parse_unit(unit: CompileUnit, root: Path, excludes: list[str], extra_args: list[str]) -> dict:
    """Parse one source file. Returns a JSON-friendly dict of facts."""
    rel_src = rel_to(unit.file, root) or unit.file
    result: dict = {"file": rel_src, "error_count": 0, "errors": [], "deps": {}}
    # Stay in the build folder until we are done: libclang reports files from relative -I paths
    # relative to it, and we turn those into repo paths below.
    if not os.path.isfile(unit.file):
        result["error_count"] = 1
        result["errors"] = [f"{rel_src}: listed in compile_commands.json but not found on disk"]
        return result
    old_cwd = os.getcwd()
    os.chdir(unit.directory if os.path.isdir(unit.directory) else str(root))
    try:
        return _collect_unit(unit, root, excludes, extra_args, rel_src, result)
    finally:
        os.chdir(old_cwd)


def _collect_unit(
    unit: CompileUnit, root: Path, excludes: list[str], extra_args: list[str], rel_src: str, result: dict
) -> dict:
    try:
        tu = _get_index().parse(unit.file, args=unit.args + extra_args)
    except cindex.TranslationUnitLoadError as e:
        result["error_count"] = 1
        result["errors"] = [f"{rel_src}: libclang could not load this file ({e})"]
        result["deps"] = {rel_src: file_hash(unit.file)}
        return result

    for d in tu.diagnostics:
        if d.severity >= cindex.Diagnostic.Error:
            result["error_count"] += 1
            if len(result["errors"]) < MAX_ERRORS_KEPT:
                loc = d.location
                where = (rel_to(loc.file.name, root) or loc.file.name) if loc.file else rel_src
                result["errors"].append(f"{where}:{loc.line}: {d.spelling}")

    col = Collector(root, excludes)
    col.collect(tu.cursor)

    deps = {rel_src: file_hash(unit.file)}
    for inc in tu.get_includes():
        r = col.rel(inc.include)
        if r is not None and r not in deps:
            deps[r] = file_hash(inc.include.name)
    result["deps"] = dict(sorted(deps.items()))
    result["functions"] = sorted(col.functions.values(), key=lambda f: (f["file"], f["line_start"], f["usr"]))
    result["variables"] = sorted(col.variables.values(), key=lambda v: (v["file"], v["line"], v["usr"]))
    result["class_bases"] = dict(sorted(col.class_bases.items()))
    result["calls"] = sorted(list(t) for t in col.calls)
    result["accesses"] = sorted(list(t) for t in col.accesses)
    result["pointer_calls"] = sorted(list(t) for t in col.pointer_calls)
    result["overrides"] = sorted(list(t) for t in col.overrides)
    result["address_taken"] = sorted([u, t] for u, t in col.address_taken.items())
    result["external_calls"] = sorted([u, f, n] for (u, f), n in col.external_calls.items())
    return result


# ---------------------------------------------------------------- outline of one file version


def function_outline(path: str, content: str, args: list[str], directory: str) -> dict | None:
    """What one file declares, as `content` gives it (an older version from git, or the file on disk).
    Returns {"functions": definitions [{usr, name, qualified_name, line_start, line_end}],
    "declarations": prototypes, same fields, "variables": globals (extern too) and fields
    [{qualified_name, line}], "errors": count}, or None when libclang could not load it.
    `path` is the file's absolute path."""
    old_cwd = os.getcwd()
    os.chdir(directory if os.path.isdir(directory) else os.path.dirname(path) or ".")
    try:
        try:
            tu = _get_index().parse(
                path,
                args=args,
                unsaved_files=[(path, content)],
                options=cindex.TranslationUnit.PARSE_INCOMPLETE,  # bodies are parsed: skipping them loses end lines
            )
        except cindex.TranslationUnitLoadError:
            return None
        target = os.path.realpath(path)
        out: dict = {"functions": [], "declarations": [], "variables": []}

        def visit(cursor: Cursor) -> None:
            for c in cursor.get_children():
                f = c.location.file
                if f is None or os.path.realpath(f.name) != target:
                    continue
                if c.kind in FUNC_KINDS:
                    out["functions" if c.is_definition() else "declarations"].append(
                        {
                            "usr": c.get_usr(),
                            "name": c.spelling,
                            "qualified_name": "::".join([*_scope_parts(c), c.spelling]),
                            "line_start": c.extent.start.line,
                            "line_end": c.extent.end.line,
                        }
                    )
                elif c.kind == K.FIELD_DECL or (c.kind == K.VAR_DECL and _variable_kind(c) == "global"):
                    if c.spelling:
                        names = [*_scope_parts(c), c.spelling]
                        out["variables"].append({"qualified_name": "::".join(names), "line": c.location.line})
                elif c.kind in DESCEND_KINDS:
                    visit(c)

        visit(tu.cursor)
        for key in ("functions", "declarations"):
            out[key].sort(key=lambda d: (d["line_start"], d["usr"]))
        out["variables"].sort(key=lambda d: (d["line"], d["qualified_name"]))
        out["errors"] = sum(1 for d in tu.diagnostics if d.severity >= cindex.Diagnostic.Error)
        return out
    finally:
        os.chdir(old_cwd)
