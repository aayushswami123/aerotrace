"""Parse one translation unit with libclang and turn it into plain facts.

The output is JSON-friendly (lists and dicts only) so it can be cached on disk and sent back from worker
processes. Nothing here decides IDs or resolves clashes; `aerotrace.index.merge` does that, because it
needs every translation unit at once.
"""

from __future__ import annotations

import contextlib
import ctypes
import hashlib
import os
import re
import threading
from collections.abc import Iterator
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
# clang stops reporting after 20 errors in a file; every error can be a use of a removed field (#29)
NO_ERROR_LIMIT = ["-ferror-limit=0"]


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


def _overloaded_decls(cursor: Cursor) -> list[Cursor]:
    """The functions an overload set (an OVERLOADED_DECL_REF) names (clang_getOverloadedDecl)."""
    lib = cindex.conf.lib
    lib.clang_getNumOverloadedDecls.argtypes = [Cursor]
    lib.clang_getNumOverloadedDecls.restype = ctypes.c_uint
    lib.clang_getOverloadedDecl.argtypes = [Cursor, ctypes.c_uint]
    lib.clang_getOverloadedDecl.restype = Cursor
    out = []
    for i in range(lib.clang_getNumOverloadedDecls(cursor)):
        d = lib.clang_getOverloadedDecl(cursor, i)
        d._tu = cursor._tu
        out.append(d)
    return out


def _starts_together(a: Cursor, b: Cursor) -> bool:
    """True when `b` (a child of `a`) starts where `a` starts: a call starts with the function it calls,
    while `T(f)` or `T x(f, 1)` in a template starts with the type."""
    return a.extent.start == b.extent.start


def _last_token(c: Cursor) -> str:
    last = ""
    for t in c.get_tokens():
        last = t.spelling
    return last


def _is_std_function_type(t) -> bool:
    """std::function and friends: calling one is a call through a pointer."""
    decl = t.get_canonical().get_declaration()
    if decl is None or decl.kind.is_invalid() or decl.spelling not in STD_FUNCTION_CLASSES:
        return False
    return "std" in _scope_parts(decl)


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
UNARY_ADDR_OF = 5  # CXUnaryOperator_AddrOf
UNARY_LNOT = 10  # CXUnaryOperator_LNot
UNARY_EXTENSION = 13  # CXUnaryOperator_Extension (__extension__)
BINARY_COMPARE_KINDS = set(range(10, 17))  # Cmp (<=>), LT, GT, LE, GE, EQ, NE
BINARY_LOGICAL_KINDS = {20, 21}  # LAnd, LOr
BINARY_ASSIGN = 22
BINARY_COMMA = 33
_COMPARE_OPS = {"<=>", "<", ">", "<=", ">=", "==", "!="}
# CXBinaryOperatorKind -> spelling, so the operator never has to be read from tokens (slow on big macros)
_BINARY_SPELLING = {
    3: "*", 4: "/", 5: "%", 6: "+", 7: "-", 8: "<<", 9: ">>", 10: "<=>", 11: "<", 12: ">", 13: "<=",
    14: ">=", 15: "==", 16: "!=", 17: "&", 18: "^", 19: "|", 20: "&&", 21: "||", 22: "=", 23: "*=",
    24: "/=", 25: "%=", 26: "+=", 27: "-=", 28: "<<=", 29: ">>=", 30: "&=", 31: "^=", 32: "|=", 33: ",",
}  # fmt: skip
# Calls that do not compile and leave nothing in the tree to link (#21, #39): only the error names the class.
_NO_CLASS_CALL = (
    ("constructor", re.compile(r"^no matching constructor for initialization of '([^']+)'")),
    ("operator()", re.compile(r"^no matching function for call to object of type '([^']+)'")),
)
_MISSING_MEMBER = re.compile(r"^no member named '([^']+)' in '(?:struct |union |class )?([^']+)'")
# {.gone = 1} after the field is removed (#25)
_MISSING_DESIGNATOR = re.compile(
    r"^field designator '([^']+)' does not refer to any field in type '(?:const |volatile )*(?:struct |union |class )?([^']+)'"
)
# a removed global, or (C++) a removed member used without this-> (#25); stored with record ""
_UNDECLARED = re.compile(r"^use of undeclared identifier '([^']+)'")

# Standard C functions that write through their first argument (memset(&x, ...), strcpy(buf, ...)).
# Matched by name, also as __builtin_NAME and the fortified __builtin___NAME_chk forms.
WRITES_FIRST_ARG = {
    "memset", "memcpy", "memmove", "memccpy", "bzero", "explicit_bzero",
    "strcpy", "strncpy", "stpcpy", "stpncpy", "strcat", "strncat", "strlcpy", "strlcat",
    "sprintf", "snprintf", "vsprintf", "vsnprintf",
    "wmemset", "wmemcpy", "wmemmove", "wcscpy", "wcsncpy", "wcscat", "wcsncat",
}  # fmt: skip
# Atomic builtins that only read the object their first argument points to.
_ATOMIC_READS = ("load", "lock_free", "thread_fence", "signal_fence", "synchronize")
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


def file_stamp(path: str, parsed: str | None) -> tuple[str, int, int]:
    """(hash, size, mtime_ns) of a file, taken together, so the index can tell later whether it changed.
    `parsed` is the hash of the content that was actually parsed (None when libclang can't tell us). If
    the file on disk is no longer that content, or changes while we hash it, return hash "" and mtime -1:
    they never match, so the next check re-indexes the file instead of trusting an old parse (#43)."""
    try:
        before = os.stat(path)
        h = file_hash(path)
        after = os.stat(path)
    except OSError:  # gone (deleted during the build): changed for sure
        return "", -1, -1
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or (
        parsed is not None and parsed != h
    ):
        return "", after.st_size, -1
    return h, after.st_size, after.st_mtime_ns


_contents_fn: list = []  # clang_getFileContents, set up once (None when this libclang lacks it)


def parsed_hash(tu: cindex.TranslationUnit, f: cindex.File) -> str | None:
    """The hash of the bytes libclang parsed for file `f` (libclang 6+), or None when it can't say."""
    if not _contents_fn:
        try:
            fn = cindex.conf.lib.clang_getFileContents
            fn.argtypes = [cindex.TranslationUnit, cindex.c_object_p, ctypes.POINTER(ctypes.c_size_t)]
            fn.restype = ctypes.c_void_p
            _contents_fn.append(fn)
        except AttributeError:
            _contents_fn.append(None)
    fn = _contents_fn[0]
    if fn is None or f is None:
        return None
    size = ctypes.c_size_t()
    ptr = fn(tu, f, ctypes.byref(size))
    if not ptr:
        return None
    return hashlib.sha256(ctypes.string_at(ptr, size.value)).hexdigest()


_UNNAMED_PATH = re.compile(
    r"(\((?:unnamed|anonymous|lambda)(?: struct| union| enum| class)? at )([^)]*?)(:\d+:\d+\))"
)


def no_machine_path(text: str) -> str:
    """Unnamed structs and unions are spelled "union (unnamed at /abs/dir/f.c:12:3)". Keep only the file
    name, "union (unnamed at f.c:12:3)", so names hold no machine path and match on every machine (#19)."""
    if " at " not in text:
        return text
    return _UNNAMED_PATH.sub(lambda m: m.group(1) + os.path.basename(m.group(2)) + m.group(3), text)


def _scope_parts(cursor: Cursor) -> list[str]:
    parts: list[str] = []
    p = cursor.semantic_parent
    while p is not None and p.kind != K.TRANSLATION_UNIT:
        if (p.kind in SCOPE_KINDS or p.kind in FUNC_KINDS) and p.spelling:
            parts.append(no_machine_path(p.spelling))
        p = p.semantic_parent
    parts.reverse()
    return parts


def _enclosing_class(cursor: Cursor) -> str | None:
    p = cursor.semantic_parent
    while p is not None and p.kind != K.TRANSLATION_UNIT:
        if p.kind in CLASS_KINDS and p.spelling:
            return "::".join([*_scope_parts(p), no_machine_path(p.spelling)])
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


def _is_deref_or_addr(c: Cursor) -> bool:
    """`*e` or `&e`."""
    if c.kind != K.UNARY_OPERATOR:
        return False
    kind = unary_kind(c)
    if kind is not None:
        return kind in (UNARY_DEREF, UNARY_ADDR_OF)
    return next((t.spelling for t in c.get_tokens()), "") in ("*", "&")


def _is_designator_expr(c: Cursor) -> bool:
    """`.f = 1` or `[2] = x` inside a braced list: libclang shows it as an unnamed expression of type void."""
    return c.kind == K.UNEXPOSED_EXPR and c.type.kind == TypeKind.VOID


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
        # (caller_usr, caller_file, callee_usr, line, dispatch, callee_name, overload): overload = one of several
        # functions with that name, because no overload matches a call that does not compile
        self.calls: set[tuple] = set()
        self.accesses: set[tuple] = set()  # (fn_usr, fn_file, var_usr, "reads"/"writes", line)
        self.pointer_calls: set[tuple] = set()  # (fn_usr, fn_file, line, function type called or "")
        # (fn_usr, fn_file, line, name called): a call in a template whose target depends on the template
        # parameters (`t.run(5)`, `T::f()`, `f(t)`). libclang shows no instantiation, so it is not linked (#33).
        self.template_calls: set[tuple] = set()
        self.overrides: set[tuple] = set()  # (usr, base_usr)
        self.address_taken: dict[str, str] = {}  # function usr -> its type, for functions used as a value
        # (caller_usr, caller_file, callee_usr or "" for a pointer call, callee_name, line, column, n_args,
        #  use of the result, detail, pointer type called or "")
        self.call_sites: set[tuple] = set()
        # (function_usr, in_function_usr or "", in_function_file, in_variable_usr or "", file, line, how)
        self.address_sites: set[tuple] = set()
        self.escapes: set[tuple] = set()  # (fn_usr, fn_file, var_usr, line, detail)
        self.initializers: set[tuple] = set()  # (global_usr, global_file, field_usr, file, line)
        # (global_usr, global_file, var_usr, line, detail): &x stored in a file-scope initializer (#27)
        self.init_escapes: set[tuple] = set()
        self._fields: dict[str, list[Cursor] | None] = {}
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
            "type": no_machine_path(c.type.spelling),
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
        name = "::".join([*_scope_parts(c), no_machine_path(c.spelling)])
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
            self.on_call(c, usr, rel, stack, idx)
        elif k in (K.MEMBER_REF_EXPR, K.DECL_REF_EXPR):
            ref = c.referenced
            if ref is not None and ref.kind in FUNC_KINDS:
                # The function named first in a call that does not compile is being called, not used as a value
                # (on_broken_call records the call). Taking it for "address taken" linked it to every pointer
                # call of its type (#14).
                if (
                    k == K.DECL_REF_EXPR
                    and not self.is_callee(stack, idx)
                    and not self.is_broken_callee(stack, idx)
                ):
                    self.mark_address_taken(ref, (usr, rel, ""), c, self.address_use(stack, idx))
                return
            if ref is not None and ref.kind == K.OVERLOADED_DECL_REF:
                # An overload set used as a value in a template (`T p = ov;`): which one is picked depends on
                # T, so every candidate may have its address taken (#31).
                if (
                    k == K.DECL_REF_EXPR
                    and not self.is_callee(stack, idx)
                    and not self.is_broken_callee(stack, idx)
                ):
                    how = self.address_use(stack, idx)
                    for d in _overloaded_decls(ref):
                        if d.kind in FUNC_KINDS:
                            self.mark_address_taken(d, (usr, rel, ""), c, how)
                return
            self.on_ref(c, stack, idx, usr, rel)
        elif k == K.MEMBER_REF and len(stack) == 1:
            self.on_ref(c, stack, idx, usr, rel, force_write=True)  # constructor initializer: `: x_(0)`
        elif k == K.UNEXPOSED_EXPR and self.broken_callee(c) is not None:
            self.on_broken_call(c, usr, rel, stack, idx)
        elif k == K.MEMBER_REF:
            if self.is_designator(stack):
                # designated initializer in a local, a static local or a compound literal: {.count = 1} (#37)
                self.on_ref(c, stack, idx, usr, rel, force_write=True)
            else:
                self.on_ref(c, stack, idx, usr, rel, force_read=True)  # offsetof(struct conn, count) (#24)
        elif k == K.INIT_LIST_EXPR and stack[-1][0].kind != K.INIT_LIST_EXPR:
            # {1, 2} sets fields by position (#26); nested lists are mapped with the outer one
            for f, value in self.positional_fields(c):
                if self.cursor_rel(f) is not None and f.get_usr():
                    self.accesses.add((usr, rel, f.get_usr(), "writes", value.location.line))
        elif k == K.VAR_DECL and _variable_kind(c) == "static_local":
            self.record_variable(c, rel, "static_local")

    @staticmethod
    def is_designator(stack: list[list]) -> bool:
        """True when the MEMBER_REF just visited (a child of stack[-1]) names a field in a designated
        initializer. libclang puts it under an unnamed expression (the designator) inside the list."""
        p = stack[-1][0]
        if p.kind == K.INIT_LIST_EXPR:
            return True
        return p.kind == K.UNEXPOSED_EXPR and len(stack) >= 2 and stack[-2][0].kind == K.INIT_LIST_EXPR

    # -- initializers that set fields by position
    def record_fields(self, t) -> list[Cursor] | None:
        """The fields of a struct or union type, in the order an initializer list fills them, or None when
        the type is not a plain C aggregate (a class with bases, an unknown type)."""
        t = t.get_canonical()
        if t.kind != TypeKind.RECORD:
            return None
        key = t.spelling
        if key in self._fields:
            return self._fields[key]
        decl = t.get_declaration()
        fields: list[Cursor] | None = None
        if not decl.kind.is_invalid() and not any(
            ch.kind == K.CXX_BASE_SPECIFIER for ch in decl.get_children()
        ):
            # unnamed bit-fields (int :3;) take no value
            fields = [f for f in t.get_fields() if not (f.is_bitfield() and not f.spelling)]
            if decl.kind == K.UNION_DECL:
                fields = fields[:1]  # {x} sets a union's first member
        self._fields[key] = fields
        return fields

    def positional_fields(self, lst: Cursor) -> list[tuple[Cursor, Cursor]]:
        """(field, value) for every field a braced list sets by position ({1, 2}), following C's rules for
        nested lists and left-out braces ({{1, 2}, 3} and {1, 2, 3} both work for a struct inside a
        struct). When a value can't be matched to a field for sure (after a designator, a value of an
        unknown type), the rest of that list is left out: Part B still lists globals of the type as
        "may set it by position" (unknown). `{0}` (set everything to zero) is not counted: no change to
        the struct's fields can break it."""
        elems = list(lst.get_children())
        if len(elems) == 1 and elems[0].kind == K.INTEGER_LITERAL and self.is_zero(elems[0]):
            return []
        out: list[tuple[Cursor, Cursor]] = []
        self._map_list(lst, elems, out, 0)
        return out

    @staticmethod
    def is_zero(e: Cursor) -> bool:
        ext = e.extent
        if ext.end.offset - ext.start.offset > 4:
            return False  # from a macro: don't read its tokens
        toks = [t.spelling for t in e.get_tokens()]
        return len(toks) == 1 and toks[0] in ("0", "0u", "0U", "0l", "0L")

    def _slots(self, t) -> tuple[list[Cursor | None], list] | None:
        """(fields or None per array element, their types) for an aggregate type; None when not one."""
        t = t.get_canonical()
        if t.kind == TypeKind.CONSTANTARRAY:
            n = t.element_count
            if n > 1 << 16:
                return None
            return [None] * n, [t.element_type] * n
        fields = self.record_fields(t)
        if fields is None:
            return None
        return list(fields), [f.type for f in fields]

    @staticmethod
    def _is_aggregate(t) -> bool:
        k = t.get_canonical().kind
        return k == TypeKind.RECORD or k in ARRAY_TYPES

    def _map_list(self, lst: Cursor, elems: list[Cursor], out: list, depth: int) -> None:
        if depth > 20:
            return
        top = self._slots(lst.type)
        if top is None:
            return
        # each frame: [fields (None for an array element), types, next index]; frames above the first are
        # aggregates whose braces were left out
        frames: list[list] = [[top[0], top[1], 0]]
        for e in elems:
            if _is_designator_expr(e) or (e.kind == K.UNEXPOSED_EXPR and e.type.kind == TypeKind.INVALID):
                return  # a designator ({.f = 1}, [2] = x) or broken code: positions after it are not followed
            while True:
                fr = frames[-1]
                if fr[2] >= len(fr[0]):
                    if len(frames) == 1:
                        return  # more values than slots (an error, or an incomplete type)
                    frames.pop()
                    continue
                f, t = fr[0][fr[2]], fr[1][fr[2]]
                tc = t.get_canonical()
                if e.kind == K.INIT_LIST_EXPR:
                    if f is not None:
                        out.append((f, e))
                    if self._is_aggregate(tc):
                        self._map_list(e, list(e.get_children()), out, depth + 1)
                    fr[2] += 1
                    break
                if not self._is_aggregate(tc):
                    if f is not None:
                        out.append((f, e))
                    fr[2] += 1
                    break
                value = e
                while value.kind in WRAPPER_KINDS:
                    kids = list(value.get_children())
                    if len(kids) != 1:
                        break
                    value = kids[0]
                vt = value.type.get_canonical()
                if vt.kind == TypeKind.INVALID:
                    return
                if vt == tc or (value.kind == K.STRING_LITERAL and tc.kind in ARRAY_TYPES):
                    if f is not None:  # the whole struct (a copy) or a char array from a string
                        out.append((f, e))
                    fr[2] += 1
                    break
                sub = self._slots(tc)
                if sub is None:
                    return
                if f is not None:
                    out.append((f, e))
                fr[2] += 1
                frames.append([sub[0], sub[1], 0])  # braces left out: the value starts filling this one

    @staticmethod
    def is_broken_callee(stack: list[list], idx: int) -> bool:
        """True when the DECL_REF_EXPR at child `idx` of stack[-1] is the function named first in a call
        that does not compile."""
        j, pos = len(stack) - 1, idx
        while j >= 0 and (stack[j][0].kind in WRAPPER_KINDS or _is_deref_or_addr(stack[j][0])):
            if pos != 0:
                return False
            if stack[j][0].kind == K.UNEXPOSED_EXPR and Collector.broken_callee(stack[j][0]) is not None:
                return True
            pos = stack[j - 1][1] - 1 if j > 0 else 0
            j -= 1
        return False

    @staticmethod
    def is_callee(stack: list[list], idx: int) -> bool:
        """True when the DECL_REF_EXPR at child position `idx` of stack[-1] is the function being called."""
        j = len(stack) - 1
        pos = idx
        # `(*f)(x)` and `(&f)(x)` call f directly, like `f(x)` (#22)
        while j >= 0 and (stack[j][0].kind in WRAPPER_KINDS or _is_deref_or_addr(stack[j][0])):
            if pos != 0:
                return False
            pos = stack[j - 1][1] - 1 if j > 0 else 0
            j -= 1
        return j >= 0 and stack[j][0].kind == K.CALL_EXPR and pos == 0

    @staticmethod
    def broken_callee(c: Cursor) -> Cursor | None:
        """The callee expression of a call that does not compile (wrong number or type of arguments), or None
        when `c` is not such a call. These are exactly the calls a signature change breaks, so they must not
        be lost. libclang keeps such a call as an unnamed expression: the callee first, then the arguments.
        - C, and C++ qualified names (`ns::f`, `K::sm`): the expression has dependent type and the callee is
          the function itself, `(f)`, `(*f)`, `&f`, or a function pointer (`p`, `(c ? f : g)`, a
          std::function, a pointer to member `(k.*pm)`).
        - C++: the expression keeps the return type. The callee is an overload set (`f(1)`, even when only
          one function has that name) or a member (`k.m(1)`, `k->m(1)`).
        Constructor calls (`K k(1, 2)`, `new K(1, 2)`) keep no reference to the class or the constructor,
        and calls on an object with operator() none to the operator: they are found from the compile error
        instead (`_class_calls`, #21, #39)."""
        if c.kind != K.UNEXPOSED_EXPR:
            return None
        first = next(c.get_children(), None)
        if first is None:
            return None
        callee = first
        # skip (f), *f, &f and the implicit function-to-pointer conversion, but never step into another call
        while (
            callee.kind == K.PAREN_EXPR
            or _is_deref_or_addr(callee)
            or (callee.kind == K.UNEXPOSED_EXPR and function_type(callee.type))
        ):
            kids = list(callee.get_children())
            if len(kids) != 1:
                break
            callee = kids[0]
        ctype = callee.type.spelling
        if callee.kind == K.DECL_REF_EXPR and ctype == "<overloaded function type>":
            pass  # C++: no function of that name matches the arguments
        elif callee.kind == K.MEMBER_REF_EXPR and ctype == "<bound member function type>":
            pass  # C++: a method, `k.m(1)`
        elif c.type.spelling != "<dependent type>":
            return None
        elif (
            callee.kind == K.DECL_REF_EXPR
            and callee.referenced is not None
            and callee.referenced.kind in FUNC_KINDS
        ):
            pass
        elif ctype == "<bound member function type>":
            pass  # through a pointer to member: (k.*pm)(1)
        elif not function_type(callee.type) and not _is_std_function_type(callee.type):
            return None  # not a call: the first part is no function and no function pointer
        # `T(f)` and `T x(f, 1)` in a template start with the type, a call starts with what it calls
        if not _starts_together(c, first):
            return None
        # `int x = f;` (a function used as a value where it does not fit) ends with the function's name, a
        # call ends after it, at the `)`
        if c.extent.end == first.extent.end and _last_token(c) != ")":
            return None
        return callee

    @staticmethod
    def callee_functions(callee: Cursor) -> list[Cursor] | None:
        """The functions a callee expression names (several for an overload set), or None when it is a
        function pointer."""
        if callee.kind not in (K.DECL_REF_EXPR, K.MEMBER_REF_EXPR):
            return None
        ref = callee.referenced
        if ref is None:
            return None
        if ref.kind in FUNC_KINDS:
            return [ref]
        if ref.kind == K.OVERLOADED_DECL_REF:
            return [d for d in _overloaded_decls(ref) if d.kind in FUNC_KINDS]
        return None

    def on_broken_call(self, c: Cursor, usr: str, rel: str, stack: list[list], idx: int) -> None:
        callee = self.broken_callee(c)
        if callee is None:
            return
        line, col = c.location.line, c.location.column
        use, detail = self.result_use(stack, idx)
        n_args = len(list(c.get_children())) - 1
        why = "the arguments do not match"
        refs = self.callee_functions(callee)
        if refs is None:
            # a call through a function pointer: still a pointer call, so it is not lost when the type changes
            ptype = function_type(callee.type)
            detail = f"does not compile: {why}" + (f"; {detail}" if detail else "")
            self.pointer_calls.add((usr, rel, line, ptype))
            self.call_sites.add((usr, rel, "", "", line, col, n_args, use, detail, ptype))
            return
        by_usr: dict[str, Cursor] = {}
        for r in refs:
            r = self.canonical(r)
            if r.get_usr():
                by_usr.setdefault(r.get_usr(), r)
        if not by_usr:
            return
        overload = len(by_usr) > 1
        if overload:
            name = next(iter(by_usr.values())).spelling
            why = f"no overload matches the arguments; it may be meant for any of the {len(by_usr)} functions named {name}"
        detail = f"does not compile: {why}" + (f"; {detail}" if detail else "")
        for target, ref in sorted(by_usr.items()):
            if self.is_builtin(ref) or (self.cursor_rel(ref) is None and not self.might_be_defined_here(ref)):
                key = (usr, rel)
                self.external_calls[key] = self.external_calls.get(key, 0) + 1
                continue
            dispatch = (
                ref.kind in (K.CXX_METHOD, K.DESTRUCTOR)
                and bool(ref.is_virtual_method())
                and self.dispatches_through(callee)
            )
            # a real call link, so callers/impact/diff still list it after a signature change (#14)
            self.calls.add((usr, rel, target, line, dispatch, ref.spelling, overload))
            self.call_sites.add((usr, rel, target, ref.spelling, line, col, n_args, use, detail, ""))

    def mark_address_taken(
        self, ref: Cursor, where: tuple[str, str, str] | None = None, at: Cursor | None = None, how: str = ""
    ) -> None:
        """`where` = (function usr, its file, global usr): the code that names the function as a value."""
        if ref.spelling.startswith("operator"):
            return  # overloaded operator calls show up as a bare reference
        ref = self.canonical(ref)
        if not ref.get_usr():
            return
        if how not in ("compared", "checked"):
            # `proc == f`, `if (f)`: the address is only looked at, so no pointer call reaches f here (#12)
            self.address_taken.setdefault(ref.get_usr(), function_type(ref.type) or "")
        if where is not None and at is not None and self.cursor_rel(ref) is not None:
            frel = self.rel(at.location.file)
            if frel is not None:
                fn_usr, fn_file, var_usr = where
                self.address_sites.add((ref.get_usr(), fn_usr, fn_file, var_usr, frel, at.location.line, how))

    def scan_initializer(self, var: Cursor) -> None:
        """Functions named in a global's initializer (handler tables) have their address taken. Designated
        initializers ({.field = value}) and values listed in order ({1, 2}) set fields. `&x` (a global or a
        field) stored there is an escape: code that uses the table can change x (#27)."""
        var_rel = self.cursor_rel(var) or ""
        var_usr = var.get_usr()

        def sets(f: Cursor, at: Cursor) -> None:
            frel = self.rel(at.location.file)
            if frel is not None and self.cursor_rel(f) is not None and f.get_usr():
                self.initializers.add((var_usr, var_rel, f.get_usr(), frel, at.location.line))

        # (cursor, its parent, its first parent that is not a cast or parentheses)
        todo = [(c, var, var) for c in var.get_children()]
        while todo:
            c, parent, real = todo.pop()
            k = c.kind
            if k == K.DECL_REF_EXPR and c.referenced is not None and c.referenced.kind in FUNC_KINDS:
                self.mark_address_taken(c.referenced, ("", "", var_usr), c, "stored in an initializer")
            elif k == K.MEMBER_REF and c.referenced is not None and c.referenced.kind == K.FIELD_DECL:
                sets(c.referenced, c)
            elif k == K.INIT_LIST_EXPR and parent.kind != K.INIT_LIST_EXPR:
                for f, value in self.positional_fields(c):
                    sets(f, value)
            elif k == K.UNARY_OPERATOR and self.is_addr_of(c):
                self.init_escape(var, var_rel, c)
            elif k in (K.DECL_REF_EXPR, K.MEMBER_REF_EXPR) and (
                real.kind in (K.INIT_LIST_EXPR, K.VAR_DECL) or _is_designator_expr(real)
            ):
                ref = c.referenced
                if ref is not None and ref.type.get_canonical().kind in ARRAY_TYPES:
                    self.init_escape(var, var_rel, c, ref)  # an array stored as a pointer: {buf}
            up = real if k in WRAPPER_KINDS and not _is_designator_expr(c) else c
            todo.extend((ch, c, up) for ch in c.get_children())

    def init_escape(self, var: Cursor, var_rel: str, at: Cursor, ref: Cursor | None = None) -> None:
        """`&x` (or an array) at `at`, inside the initializer of the global `var`: record where x's address
        goes. The row is kept under the global (it has no function), with the line in the global's file."""
        if ref is None:
            e = next(at.get_children(), None)
            while e is not None and (
                e.kind in WRAPPER_KINDS or (e.kind == K.ARRAY_SUBSCRIPT_EXPR and self.subscripts_array(e))
            ):
                e = next(e.get_children(), None)
            if e is None or e.kind not in (K.DECL_REF_EXPR, K.MEMBER_REF_EXPR):
                return
            ref = e.referenced
        if ref is None or not (ref.kind == K.FIELD_DECL or (ref.kind == K.VAR_DECL and _variable_kind(ref))):
            return
        if self.cursor_rel(ref) is None or not ref.get_usr() or not var.get_usr():
            return
        name = var.spelling
        frel = self.rel(at.location.file)
        line = at.location.line
        detail = f"address stored in the initializer of {name}"
        if frel != var_rel:
            # the value comes from an included file: name the place, and keep a line of var's file
            detail += f" (at {frel or 'another file'}:{line})"
            line = var.location.line
        self.init_escapes.add((var.get_usr(), var_rel, ref.get_usr(), line, detail))

    @staticmethod
    def subscripts_array(e: Cursor) -> bool:
        """`a[i]` where `a` itself is an array (not a pointer): &a[i] is an address inside a."""
        base = next(e.get_children(), None)
        while base is not None and base.kind in WRAPPER_KINDS:
            base = next(base.get_children(), None)
        return base is not None and base.type.get_canonical().kind in ARRAY_TYPES

    def canonical(self, ref: Cursor) -> Cursor:
        for _ in range(5):
            t = _template_of(ref)
            if t is None:
                break
            ref = t
        return ref

    @staticmethod
    def in_template(stack: list[list]) -> bool:
        """True inside a template or a lambda (which may be generic), where dependent types are normal."""
        if any(e[0].kind == K.LAMBDA_EXPR for e in stack):
            return True
        p: Cursor | None = stack[0][0]
        for _ in range(50):
            if p is None or p.kind == K.TRANSLATION_UNIT:
                return False
            if p.kind in (K.FUNCTION_TEMPLATE, K.CLASS_TEMPLATE, K.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION):
                return True
            p = p.semantic_parent
        return True

    @staticmethod
    def named_through_deref(call: Cursor) -> Cursor | None:
        """The function in `(*f)(x)` or `(&f)(x)`: a direct call (#22). None for anything else."""
        callee = next(call.get_children(), None)
        seen_op = False
        while callee is not None and (callee.kind in WRAPPER_KINDS or _is_deref_or_addr(callee)):
            seen_op = seen_op or callee.kind == K.UNARY_OPERATOR
            kids = list(callee.get_children())
            callee = kids[0] if len(kids) == 1 else None
        if seen_op and callee is not None and callee.kind == K.DECL_REF_EXPR:
            r = callee.referenced
            if r is not None and r.kind in (K.FUNCTION_DECL, K.CXX_METHOD):
                return r
        return None

    def on_call(self, c: Cursor, usr: str, rel: str, stack: list[list] | None = None, idx: int = 0) -> None:
        ref = c.referenced
        if ref is None or ref.kind not in FUNC_KINDS:
            ref = self.named_through_deref(c) or ref
        line = c.location.line
        site = None
        if stack is not None:
            use, detail = self.result_use(stack, idx)
            try:
                n_args = len(list(c.get_arguments()))
            except Exception:
                n_args = -1
            if c.type.spelling == "<dependent type>" and not self.in_template(stack):
                # outside a template, only an error gives a call a dependent type: two(two(1), 2, 3)
                detail = "does not compile: an argument does not match" + (f"; {detail}" if detail else "")
            site = (line, c.location.column, n_args, use, detail)
        if ref is None or ref.kind not in FUNC_KINDS or self.is_std_function(ref):
            if ref is None and stack is not None and self.in_template(stack):
                name = self.dependent_name(c)
                if name:
                    self.template_calls.add((usr, rel, line, name))
            # through a pointer, std::function or a dependent name: remember the type that is called
            ptype = self.called_type(c)
            self.pointer_calls.add((usr, rel, line, ptype))
            if site:
                self.call_sites.add((usr, rel, "", "", *site, ptype))
            return
        ref = self.canonical(ref)
        target = ref.get_usr()
        if not target:
            self.pointer_calls.add((usr, rel, line, ""))
            if site:
                self.call_sites.add((usr, rel, "", "", *site, ""))
            return
        if self.is_builtin(ref) or (self.cursor_rel(ref) is None and not self.might_be_defined_here(ref)):
            key = (usr, rel)
            self.external_calls[key] = self.external_calls.get(key, 0) + 1
            return
        dispatch = ref.kind in (K.CXX_METHOD, K.DESTRUCTOR) and ref.is_virtual_method() and self.dispatches(c)
        self.calls.add((usr, rel, target, line, dispatch, ref.spelling, False))
        if site:
            self.call_sites.add((usr, rel, target, ref.spelling, *site, ""))

    @staticmethod
    def dependent_name(call: Cursor) -> str:
        """The name a call in a template calls when it depends on the template parameters (`t.run(5)` ->
        'run', `T::sm(1)` -> 'sm', `f(t)` -> 'f'), or '' for a call through an object or a pointer."""
        callee = next(call.get_children(), None)
        while callee is not None and callee.kind in WRAPPER_KINDS:
            kids = list(callee.get_children())
            callee = kids[0] if len(kids) == 1 else None
        if callee is None or callee.kind not in (K.DECL_REF_EXPR, K.MEMBER_REF_EXPR):
            return ""
        r = callee.referenced
        if r is not None:
            return r.spelling if r.kind == K.OVERLOADED_DECL_REF else ""
        if callee.spelling:
            return callee.spelling
        name, depth = "", 0  # the last name outside template arguments: `t.template get<int>` -> get
        for t in callee.get_tokens():
            if t.spelling == "<":
                depth += 1
            elif t.spelling == ">":
                depth = max(0, depth - 1)
            elif t.spelling == ">>":
                depth = max(0, depth - 2)
            elif depth == 0 and t.kind == TokenKind.IDENTIFIER:
                name = t.spelling
        return "" if name.startswith("operator") else name

    # -- how a value is used (the result of a call, or a function named as a value)
    @staticmethod
    def _up(stack: list[list], j: int, pos: int) -> tuple[Cursor | None, int, int]:
        """The first real parent at or above stack[j] (child position `pos`), skipping parentheses, implicit
        conversions and non-void casts. Returns (parent, position of the value in it, its stack index)."""
        while j >= 0:
            p = stack[j][0]
            wrapper = p.kind == K.PAREN_EXPR or (
                p.kind == K.UNEXPOSED_EXPR and len(list(p.get_children())) == 1
            )
            if p.kind == K.CSTYLE_CAST_EXPR and p.type.kind != TypeKind.VOID:
                wrapper = True
            if p.kind == K.UNARY_OPERATOR and unary_kind(p) == UNARY_EXTENSION:
                wrapper = True
            if not wrapper:
                return p, pos, j
            pos = stack[j - 1][1] - 1 if j > 0 else 0
            j -= 1
        return None, 0, -1

    @staticmethod
    def _describe(target: Cursor | None) -> str:
        """A short name for where a value is stored: `x`, `field n`, `an array element`."""
        while target is not None and target.kind in WRAPPER_KINDS:
            target = next(target.get_children(), None)
        if target is None:
            return "an expression"
        if target.kind == K.DECL_REF_EXPR:
            return target.spelling
        if target.kind == K.MEMBER_REF_EXPR:
            return f"field {target.spelling}"
        if target.kind == K.ARRAY_SUBSCRIPT_EXPR:
            return "an array element"
        if target.kind == K.UNARY_OPERATOR:
            return "*pointer"
        return "an expression"

    def result_use(self, stack: list[list], idx: int) -> tuple[str, str]:
        """How the value of the call at child `idx` of stack[-1] is used:
        ignored | discarded (cast to void) | stored | compared | checked (true/false) | passed | returned |
        called | used. With a short detail (where it is stored, which function gets it, ...)."""
        j, pos = len(stack) - 1, idx
        while True:
            p, pos, j = self._up(stack, j, pos)
            if p is None:
                return "ignored", ""
            k = p.kind
            if k in (K.COMPOUND_STMT, K.LABEL_STMT, K.DEFAULT_STMT) or (k == K.CASE_STMT and pos > 0):
                return "ignored", ""
            if k == K.CSTYLE_CAST_EXPR:
                return "discarded", "cast to (void)"
            if k == K.RETURN_STMT:
                return "returned", ""
            if k == K.VAR_DECL:
                return "stored", f"in {p.spelling}"
            if k == K.CALL_EXPR:
                if pos == 0:
                    return "called", "the result is called as a function"
                callee = p.referenced.spelling if p.referenced is not None else "a function pointer"
                return "passed", f"to {callee}() as argument {pos}"
            if k == K.UNEXPOSED_EXPR and pos > 0:
                broken = self.broken_callee(p)  # an argument of a call that does not compile
                if broken is not None:
                    fns = self.callee_functions(broken)
                    callee = fns[0].spelling if fns else "a function pointer"
                    return "passed", f"to {callee}() as argument {pos}"
            if k in (K.IF_STMT, K.WHILE_STMT, K.SWITCH_STMT):
                return ("checked", "") if pos == 0 else ("ignored", "")
            if k == K.DO_STMT:
                return ("checked", "") if pos == 1 else ("ignored", "")
            if k == K.FOR_STMT:
                return "used", "in a for loop header"
            if k == K.CONDITIONAL_OPERATOR:
                if pos == 0:
                    return "checked", ""
                j, pos = j - 1, (stack[j - 1][1] - 1 if j > 0 else 0)  # the chosen value flows on
                continue
            if k == K.BINARY_OPERATOR:
                kind = binary_kind(p)
                op = _BINARY_SPELLING.get(kind, "") if kind is not None else (self.binary_op(p) or "")
                if kind == BINARY_ASSIGN or (kind is None and op == "="):
                    lhs = next(p.get_children(), None)
                    stored = f"in {self._describe(lhs)}"
                    # `(o = lookup(...)) == NULL`: the assignment's own value is used too
                    then, then_detail = self.result_use(stack[:j], stack[j - 1][1] - 1 if j > 0 else 0)
                    if then in ("compared", "checked"):
                        return "stored", f"{stored}, then {then}" + (f" {then_detail}" if then_detail else "")
                    return "stored", stored
                if kind in BINARY_COMPARE_KINDS or (kind is None and op in _COMPARE_OPS):
                    return "compared", f"with {op}" if op else ""
                if kind in BINARY_LOGICAL_KINDS or (kind is None and op in ("&&", "||")):
                    return "checked", ""
                if kind == BINARY_COMMA or (kind is None and op == ","):
                    if pos == 0:
                        return "ignored", ""
                    j, pos = j - 1, (stack[j - 1][1] - 1 if j > 0 else 0)
                    continue
                return "used", f"in arithmetic ({op})" if op else "in arithmetic"
            if k == K.COMPOUND_ASSIGNMENT_OPERATOR:
                lhs = next(p.get_children(), None)
                kind = binary_kind(p)
                op = _BINARY_SPELLING.get(kind, "") if kind is not None else self.binary_op(p)
                return "stored", f"in {self._describe(lhs)} ({op or 'op='})"
            if k == K.UNARY_OPERATOR:
                kind = unary_kind(p)
                if kind == UNARY_LNOT:
                    return "checked", "negated with !"
                if kind == UNARY_DEREF:
                    return "used", "dereferenced"
                return "used", "in arithmetic"
            if k == K.INIT_LIST_EXPR:
                return "stored", "in an initializer"
            if k in (K.MEMBER_REF_EXPR, K.ARRAY_SUBSCRIPT_EXPR):
                return "used", "dereferenced"
            return "used", ""

    def address_use(self, stack: list[list], idx: int) -> str:
        """Where a function named as a value goes: stored, passed to a function, compared, ..."""
        use, detail = self.result_use(stack, idx)
        if use == "stored":
            return f"stored {detail}"
        if use == "passed":
            return f"passed {detail}"
        return use

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
        return self.dispatches_through(next(call.get_children(), None))

    def dispatches_through(self, callee: Cursor | None) -> bool:
        """`dispatches`, given the callee expression (`b->area`)."""
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
        self,
        c: Cursor,
        stack: list[list],
        idx: int,
        usr: str,
        rel: str,
        force_write: bool = False,
        force_read: bool = False,
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
        line = c.location.line
        how = "write" if force_write else "read" if force_read else self.access_of(stack, idx, ref)
        if how in ("write", "update"):
            self.accesses.add((usr, rel, target, "writes", line))
        if how in ("read", "update"):  # x++ and x += n read the old value too (#52)
            self.accesses.add((usr, rel, target, "reads", line))
        if how == "read" and not force_read:
            where = self.escape_of(stack, idx, ref)
            if where:
                self.escapes.add((usr, rel, target, line, where))

    def escape_of(self, stack: list[list], idx: int, ref: Cursor) -> str | None:
        """Where the address of a global or field goes when we can't see what happens to it there:
        `foo(&x)` -> 'passed to foo()', `p = &x` -> 'stored in a pointer', `return &x`, or an array passed
        to a function. None when the address does not leave (no '&', a comparison, an atomic, ...)."""
        is_array = ref.type.get_canonical().kind in ARRAY_TYPES
        j, pos = len(stack) - 1, idx
        while j >= 0:
            pc = stack[j][0]
            if (pc.kind in WRAPPER_KINDS and len(list(pc.get_children())) == 1) or (
                pc.kind == K.ARRAY_SUBSCRIPT_EXPR and is_array and pos == 0
            ):
                if pc.kind == K.ARRAY_SUBSCRIPT_EXPR:
                    # close(a[0]) passes an element by value, not the array (#18); &a[0] still escapes
                    is_array = pc.type.get_canonical().kind in ARRAY_TYPES
                pos = stack[j - 1][1] - 1 if j > 0 else 0
                j -= 1
                continue
            break
        if j < 0:
            return None
        pc = stack[j][0]
        if pc.kind == K.UNARY_OPERATOR and pos == 0 and self.is_addr_of(pc):
            use, detail = self.result_use(stack[:j], stack[j - 1][1] - 1 if j > 0 else 0)
        elif is_array and pc.kind == K.CALL_EXPR and pos >= 1:
            use, detail = (
                "passed",
                f"to {pc.referenced.spelling if pc.referenced is not None else 'a function pointer'}()",
            )
        else:
            return None
        if use == "passed":
            if detail.startswith(("to __builtin_", "to __sync_", "to __atomic_")):
                return None  # a compiler builtin (object size, prefetch, ...) does not keep the address
            if self.writes_first_arg_of(stack, j, pc):
                return None  # memset-style: recorded as a write
            return f"passed {detail.split(' as argument')[0]}"
        if use in ("stored", "returned"):
            return f"address {use}" + (f" {detail}" if detail else "")
        return None

    def writes_first_arg_of(self, stack: list[list], j: int, pc: Cursor) -> bool:
        """Whether the call that receives the value at stack[j] is a known writer (memset, atomics), which
        is recorded as a write, not an escape."""
        k = j
        while k >= 0 and stack[k][0].kind != K.CALL_EXPR:
            k -= 1
        return k >= 0 and self.writes_first_arg(stack[k][0])

    # -- read or write?
    def access_of(self, stack: list[list], idx: int, ref: Cursor) -> str:
        """How the code at child `idx` of stack[-1] uses the variable: read | write | update (x++, x += n:
        reads the old value and writes a new one)."""
        is_array = ref.type.get_canonical().kind in ARRAY_TYPES
        j = len(stack) - 1
        pos = idx
        while j >= 0:
            pc = stack[j][0]
            if pc.kind in WRAPPER_KINDS or (pc.kind == K.ARRAY_SUBSCRIPT_EXPR and is_array and pos == 0):
                if pc.kind == K.ARRAY_SUBSCRIPT_EXPR:
                    is_array = pc.type.get_canonical().kind in ARRAY_TYPES  # a[i] is an element (#18)
                pos = stack[j - 1][1] - 1 if j > 0 else 0
                j -= 1
            else:
                break
        if j < 0:
            return "read"
        pc = stack[j][0]
        if pc.kind == K.CALL_EXPR:
            if pos == 1 and is_array and self.writes_first_arg(pc):
                return "write"  # memset(arr, 0, sizeof arr): the array decays to a pointer
            # libclang lists the object an overloaded operator acts on first
            r = pc.referenced
            if pos == 0 and r is not None and r.spelling in OVERLOADED_WRITE_OPS:
                return "write" if r.spelling == "operator=" else "update"
            return "read"
        if pos != 0:
            return "read"
        if pc.kind == K.UNARY_OPERATOR and self.is_addr_of(pc):
            return "write" if self.address_written(stack, j) else "read"
        if pc.kind == K.COMPOUND_ASSIGNMENT_OPERATOR:
            return "update"
        if pc.kind == K.BINARY_OPERATOR:
            kind = binary_kind(pc)
            if kind is not None:
                if kind == BINARY_ASSIGN:
                    return "write"
                return "update" if kind in BINARY_ASSIGN_KINDS else "read"
            op = self.binary_op(pc)
            return "write" if op == "=" else "update" if op in ASSIGN_OPS else "read"
        if pc.kind == K.UNARY_OPERATOR:
            kind = unary_kind(pc)
            if kind is not None:
                return "update" if kind in UNARY_INC_DEC_KINDS else "read"
            toks = [t.spelling for t in pc.get_tokens()]
            return "update" if toks and (toks[0] in ("++", "--") or toks[-1] in ("++", "--")) else "read"
        return "read"

    @staticmethod
    def is_addr_of(c: Cursor) -> bool:
        kind = unary_kind(c)
        if kind is not None:
            return kind == UNARY_ADDR_OF
        return next((t.spelling for t in c.get_tokens()), "") == "&"

    @staticmethod
    def writes_first_arg(call: Cursor) -> bool:
        """Whether a call writes through its first argument: an atomic or __sync builtin that changes the
        object, or a standard C function such as memset or strcpy."""
        r = call.referenced
        name = (r.spelling if r is not None else call.spelling) or ""
        if name.startswith(("__sync_", "__atomic_", "__c11_atomic_")):
            return not any(w in name for w in _ATOMIC_READS)
        if name.startswith("__builtin___") and name.endswith("_chk"):
            name = name[len("__builtin___") : -len("_chk")]
        elif name.startswith("__builtin_"):
            name = name[len("__builtin_") :]
        return name in WRITES_FIRST_ARG

    def address_written(self, stack: list[list], j: int) -> bool:
        """`&x` sits at stack[j]. Is x written by what receives the address?

        - an atomic expression (C11 atomic_*, __atomic_*): libclang does not name them, but a load has two
          operands (object, order) and every operation that changes the object has more;
        - a builtin or standard C function that writes through its first argument (see writes_first_arg).
        A pointer passed to any other function is not counted: what the callee does is not known here.
        """
        k = j - 1
        pos = stack[j - 1][1] - 1 if j > 0 else 0
        while k >= 0:
            pc = stack[k][0]
            if pc.kind == K.PAREN_EXPR or (pc.kind == K.UNEXPOSED_EXPR and len(list(pc.get_children())) == 1):
                pos = stack[k - 1][1] - 1 if k > 0 else 0  # an implicit cast, e.g. to void *
                k -= 1
                continue
            break
        if k < 0:
            return False
        pc = stack[k][0]
        if pc.kind == K.UNEXPOSED_EXPR and pos == 0:
            return len(list(pc.get_children())) > 2  # an atomic expression
        return pc.kind == K.CALL_EXPR and pos == 1 and self.writes_first_arg(pc)

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


# Messages clang gives as warnings (C) that GCC 14+ gives as errors (-Wincompatible-pointer-types,
# -Wimplicit-function-declaration, -Wint-conversion). Kept in outline diagnostics (#77 review).
GCC14_ERRORS = (
    "incompatible pointer types",
    "incompatible integer to pointer conversion",
    "incompatible pointer to integer conversion",
    "implicit declaration of function",
    "call to undeclared function",
)

# The working folder is shared by every thread of the process, and libclang resolves relative -I paths and
# include names against it. Every in-process parse changes it, so they take turns (#46). Index builds parse
# in child processes and are not slowed down by this.
_CWD_LOCK = threading.RLock()


@contextlib.contextmanager
def _in_folder(folder: str) -> Iterator[None]:
    """Work in `folder`, holding the process-wide lock until the old working folder is back."""
    with _CWD_LOCK:
        old = os.getcwd()
        os.chdir(folder)
        try:
            yield
        finally:
            os.chdir(old)


def parse_unit(unit: CompileUnit, root: Path, excludes: list[str], extra_args: list[str]) -> dict:
    """Parse one source file. Returns a JSON-friendly dict of facts."""
    rel_src = rel_to(unit.file, root) or unit.file
    result: dict = {"file": rel_src, "error_count": 0, "errors": [], "deps": {}, "stats": {}}
    # Stay in the build folder until we are done: libclang reports files from relative -I paths
    # relative to it, and we turn those into repo paths below.
    if not os.path.isfile(unit.file):
        result["error_count"] = 1
        result["errors"] = [f"{rel_src}: listed in compile_commands.json but not found on disk"]
        return result
    with _in_folder(unit.directory if os.path.isdir(unit.directory) else str(root)):
        return _collect_unit(unit, root, excludes, extra_args, rel_src, result)


_UNKNOWN_FLAG = re.compile(
    r"^(?:unknown argument|unsupported option|unknown target CPU|unknown target ABI)\b.*?'([^']+)'"
)
_MISSING_HEADER = re.compile(r"^'(.+)' file not found")


def without_machine_flags(args: list[str]) -> tuple[list[str], list[str]]:
    """Drop target-specific `-m...` flags (and `-march`/`-mcpu` values). Returns (kept, dropped)."""
    kept: list[str] = []
    dropped: list[str] = []
    for a in args:
        (dropped if a.startswith("-m") and a not in ("-m32", "-m64") else kept).append(a)
    return kept, dropped


def _class_name(spelled: str) -> str:
    """'const struct ns::K<int>' -> 'ns::K': the class as the index names it (`container`)."""
    words = [w for w in spelled.split() if w not in ("const", "volatile", "struct", "class", "union")]
    name = " ".join(words)
    if "<" in name:
        name = name[: name.index("<")]
    return name.strip()


def _class_calls(col: Collector, found: set[tuple]) -> list[list]:
    """[caller usr, caller file, class, line, column, what] for each broken constructor or operator() call
    inside a function of the repo: the innermost function whose lines hold the error."""
    out = []
    for file, line, column, what, cls in sorted(found):
        parts = cls.split("::")
        if what == "operator()" and parts[0] == "std" and parts[-1] in STD_FUNCTION_CLASSES:
            # std::function and friends: already a call through a pointer, with its own blind spot (#14).
            # Seen once every compile error is read (#29).
            continue
        inside = [
            f
            for f in col.functions.values()
            if f["file"] == file and f["line_start"] <= line <= f["line_end"] and not f["is_pure_virtual"]
        ]
        if inside and cls:
            f = min(inside, key=lambda f: (f["line_end"] - f["line_start"], f["usr"]))
            out.append([f["usr"], f["file"], cls, line, column, what])
    return out


def _collect_unit(
    unit: CompileUnit,
    root: Path,
    excludes: list[str],
    extra_args: list[str],
    rel_src: str,
    result: dict,
) -> dict:
    result["ignored_flags"] = []
    result["dropped_flags"] = []
    result["missing_headers"] = []
    try:
        tu = _get_index().parse(unit.file, args=unit.args + extra_args + NO_ERROR_LIMIT)
    except cindex.TranslationUnitLoadError:
        # Usually a flag clang refuses for this target (-mfloat-abi=hard on the host). Try once without them.
        kept, dropped = without_machine_flags(unit.args)
        try:
            if not dropped:
                raise
            tu = _get_index().parse(unit.file, args=kept + extra_args + NO_ERROR_LIMIT)
            result["dropped_flags"] = sorted(set(dropped))
        except cindex.TranslationUnitLoadError as e:
            result["error_count"] = 1
            result["errors"] = [f"{rel_src}: libclang could not load this file ({e})"]
            h, size, mtime = file_stamp(unit.file, None)
            result["deps"] = {rel_src: h}
            result["stats"] = {rel_src: [size, mtime]}
            return result

    ignored: set[str] = set()
    missing: set[str] = set()
    members: set[tuple] = set()
    class_calls: set[tuple] = set()  # (file, line, column, "constructor" | "operator()", class)
    for d in tu.diagnostics:
        if d.severity < cindex.Diagnostic.Error:
            continue
        m = _UNKNOWN_FLAG.match(d.spelling)
        if m and not d.location.file:  # a flag clang does not know (GCC-only); it does not stop the parse
            ignored.add(m.group(1))
            continue
        h = _MISSING_HEADER.match(d.spelling)
        if h:
            missing.add(h.group(1))
        for what, rx in _NO_CLASS_CALL:
            cm = rx.match(d.spelling)
            if cm and d.location.file is not None:
                where = rel_to(d.location.file.name, root)
                if where is not None:
                    class_calls.add(
                        (where, d.location.line, d.location.column, what, _class_name(cm.group(1)))
                    )
        if d.location.file is not None:
            mm = _MISSING_MEMBER.match(d.spelling) or _MISSING_DESIGNATOR.match(d.spelling)
            un = None if mm else _UNDECLARED.match(d.spelling)
            if mm or un:
                where = rel_to(d.location.file.name, root)
                if where is not None:
                    if mm:
                        members.add((where, d.location.line, mm.group(1), no_machine_path(mm.group(2))))
                    else:
                        members.add((where, d.location.line, un.group(1), ""))
        result["error_count"] += 1
        if len(result["errors"]) < MAX_ERRORS_KEPT:
            loc = d.location
            where = (rel_to(loc.file.name, root) or loc.file.name) if loc.file else rel_src
            result["errors"].append(f"{where}:{loc.line}: {d.spelling}")
    result["ignored_flags"] = sorted(ignored)
    result["missing_headers"] = sorted(missing)
    result["missing_members"] = sorted(list(t) for t in members)

    col = Collector(root, excludes)
    col.collect(tu.cursor)

    stamps = {rel_src: file_stamp(unit.file, parsed_hash(tu, tu.get_file(unit.file)))}
    for inc in tu.get_includes():
        r = col.rel(inc.include)
        if r is not None and r not in stamps:
            stamps[r] = file_stamp(inc.include.name, parsed_hash(tu, inc.include))
    result["deps"] = {r: stamps[r][0] for r in sorted(stamps)}
    result["stats"] = {r: [stamps[r][1], stamps[r][2]] for r in sorted(stamps)}
    result["functions"] = sorted(col.functions.values(), key=lambda f: (f["file"], f["line_start"], f["usr"]))
    result["variables"] = sorted(col.variables.values(), key=lambda v: (v["file"], v["line"], v["usr"]))
    result["class_bases"] = dict(sorted(col.class_bases.items()))
    result["calls"] = sorted(list(t) for t in col.calls)
    result["accesses"] = sorted(list(t) for t in col.accesses)
    result["pointer_calls"] = sorted(list(t) for t in col.pointer_calls)
    result["template_calls"] = sorted(list(t) for t in col.template_calls)
    result["class_calls"] = _class_calls(col, class_calls)
    result["overrides"] = sorted(list(t) for t in col.overrides)
    result["address_taken"] = sorted([u, t] for u, t in col.address_taken.items())
    result["call_sites"] = sorted(list(t) for t in col.call_sites)
    result["address_sites"] = sorted(list(t) for t in col.address_sites)
    result["escapes"] = sorted(list(t) for t in col.escapes)
    result["initializers"] = sorted(list(t) for t in col.initializers)
    result["init_escapes"] = sorted(list(t) for t in col.init_escapes)
    result["external_calls"] = sorted([u, f, n] for (u, f), n in col.external_calls.items())
    return result


# ---------------------------------------------------------------- outline of one file version


_UNNAMED_AT = re.compile(r"\((unnamed|anonymous)( struct| union| enum| class)? at [^)]*\)")


def _norm(text: str) -> str:
    """Spaces collapsed, and the place of unnamed structs and unions left out ("(unnamed union at
    /abs/f.c:12:3)" -> "(unnamed union)"): it moves when lines are added above, and holds a machine path."""
    return _UNNAMED_AT.sub(lambda m: f"({m.group(1)}{m.group(2) or ''})", " ".join(text.split()))


def _digest(text: str) -> str:
    return hashlib.sha256(_norm(text).encode("utf-8", "surrogateescape")).hexdigest()[:16]


INLINE_WORDS = {"inline", "__inline", "__inline__", "constexpr", "consteval"}
WEAK_WORDS = {"weak", "__weak__", "selectany"}


_CONDITIONAL = re.compile(r"^[ \t]*#[ \t]*(if|ifdef|ifndef|elif|else)\b", re.M)
_COMMENTS = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)


def _specifier_words(data: bytes, start: int, name_at: int) -> set[str]:
    """The words written before a function's name (its storage class, `inline`, attributes), read from the
    definition's own text with comments removed. Nothing above the definition is read."""
    if not 0 <= start <= name_at <= len(data):
        return set()
    text = _COMMENTS.sub(" ", data[start:name_at].decode("utf-8", "surrogateescape"))
    return set(re.findall(r"[A-Za-z_]\w*", text))


def _body_refs(c: Cursor) -> tuple[list[str], list[str]]:
    """What a function body refers to, as the compiler resolved it (macros expanded): the USRs of the
    functions and variables, and the names it could not resolve (templates, errors)."""
    refs: set[str] = set()
    unresolved: set[str] = set()
    for d in c.walk_preorder():
        r = d.referenced
        if r is not None and r != d:
            usr = r.get_usr()
            if usr:
                refs.add(usr)
        elif r is None and d.spelling and (d.kind.is_expression() or d.kind.is_reference()):
            unresolved.add(d.spelling)
    return sorted(refs), sorted(unresolved)


def _outline_function(c: Cursor, data: bytes) -> dict:
    """What a definition or prototype looks like: its signature, parameters and head text."""
    sig = c.displayname
    if c.kind not in (K.CONSTRUCTOR, K.DESTRUCTOR, K.CONVERSION_FUNCTION):
        sig = f"{c.result_type.spelling} {sig}"
    if c.kind in (K.CXX_METHOD, K.CONVERSION_FUNCTION) and c.is_const_method():
        sig += " const"
    params = [
        {
            "name": a.spelling,
            "type": _norm(a.type.spelling),
            "canonical": _norm(a.type.get_canonical().spelling),
            "has_default": any(ch.kind.is_expression() for ch in a.get_children()),
        }
        for a in (c.get_arguments() or [])
    ]
    proto = c.type.kind == TypeKind.FUNCTIONPROTO
    start = c.extent.start.offset
    body = next((ch for ch in c.get_children() if ch.kind == K.COMPOUND_STMT), None)
    end = body.extent.start.offset if body is not None else c.extent.end.offset
    head = data[start:end].decode("utf-8", "surrogateescape") if 0 <= start <= end <= len(data) else ""
    if body is not None and ";" in head:
        proto = False  # a K&R definition, `int f(x) int x; {`: clang types it as a prototype, callers don't
    for i, prm in enumerate(params):
        if prm["name"]:
            head = re.sub(rf"\b{re.escape(prm['name'])}\b", f"${i}", head)
    words = _specifier_words(data, start, c.location.offset)
    in_class = (
        c.kind in METHOD_KINDS
        and c.lexical_parent is not None
        and c.lexical_parent == c.semantic_parent
        and c.semantic_parent.kind in CLASS_KINDS
    )
    attrs = any(ch.kind.is_attribute() for ch in c.get_children())
    return {
        "usr": c.get_usr(),
        "name": c.spelling,
        "qualified_name": "::".join([*_scope_parts(c), c.spelling]),
        "line_start": c.extent.start.line,
        "line_end": c.extent.end.line,
        "head_end": body.extent.start.line if body is not None else c.extent.end.line,
        "signature": sig,
        "returns": _norm(c.result_type.spelling),
        "returns_canonical": _norm(c.result_type.get_canonical().spelling),
        "params": params,
        "variadic": bool(proto and c.type.is_function_variadic()),
        "has_prototype": proto or c.kind != K.FUNCTION_DECL,
        "static": c.storage_class == StorageClass.STATIC,
        "extern": c.storage_class == StorageClass.EXTERN,
        # inline (or implicitly: constexpr, a method defined in its class): may be defined in many files
        "inline": bool(words & INLINE_WORDS) or in_class,
        "weak": bool(words & WEAK_WORDS),
        "attrs": attrs and not (words & WEAK_WORDS),  # an attribute that is not provably harmless
        "template": c.kind == K.FUNCTION_TEMPLATE,
        "head": _digest(head),
    }


def _outline_variable(c: Cursor, kind: str, position: int, data: bytes) -> dict:
    """A field or global: its type, and hashes of its declaration text and of its initializer."""
    start, end = c.extent.start.offset, c.extent.end.offset
    init_at = None
    if c.kind == K.VAR_DECL:
        exprs = [ch for ch in c.get_children() if ch.kind.is_expression()]
        if exprs:
            at = exprs[-1].extent.start.offset
            name_at = c.location.offset
            if start <= name_at <= at <= end and b"=" in data[name_at:at]:
                init_at = at
    ok = 0 <= start <= end <= len(data)
    decl = data[start : init_at if init_at is not None else end] if ok else b""
    init = data[init_at:end] if ok and init_at is not None else b""
    container = _enclosing_class(c)
    return {
        "usr": c.get_usr(),
        "name": c.spelling,
        "qualified_name": "::".join([*_scope_parts(c), c.spelling]),  # matches the index
        "container": _norm(container) if container else container,
        "kind": kind,
        "type": _norm(c.type.spelling),
        "canonical": _norm(c.type.get_canonical().spelling),
        "line": c.location.line,
        "line_end": c.extent.end.line,
        "position": position,
        "decl": _digest(decl.decode("utf-8", "surrogateescape")),
        "init": _digest(init.decode("utf-8", "surrogateescape")) if init_at is not None else "",
    }


def function_outline(
    path: str,
    content: str,
    args: list[str],
    directory: str,
    others: list[tuple[str, str]] | None = None,
    ref_lines: frozenset[int] | None = None,
) -> dict | None:
    """What one file declares, as `content` gives it (an older version, the file on disk, or a patched
    version in memory). Nothing is stored. Returns:

    - "functions": definitions, "declarations": prototypes. Each {usr, name, qualified_name, line_start,
      line_end, head_end, signature, returns, params [{name, type, canonical, has_default}], variadic,
      has_prototype, static, extern, inline, weak, attrs, template, head (hash of the head text, parameter
      names left out)}. Definitions that contain a line of `ref_lines` also have refs (USRs the body refers
      to) and unresolved (names it could not resolve).
    - "variables": globals (extern too) and fields: {usr, name, qualified_name, container, kind, type,
      canonical, line, line_end, position (in its record), decl (hash of the declaration text), init
      (hash of the initializer, "" when there is none)}.
    - "errors": count; "diagnostics": errors in this file [{line, text}]; "protos": prototypes seen in
      other files of the translation unit, {qualified_name: [signature, ...]}; "noproto": names declared
      there without a prototype (`int f();` in C).

    None when libclang could not load it. `path` is the file's absolute path. `others`: (absolute path,
    content) of other files of the same version (a patched header), used instead of the files on disk."""
    with _in_folder(directory if os.path.isdir(directory) else os.path.dirname(path) or "."):
        return _outline_here(path, content, args, others, ref_lines)


def _outline_here(
    path: str,
    content: str,
    args: list[str],
    others: list[tuple[str, str]] | None,
    ref_lines: frozenset[int] | None = None,
) -> dict | None:
    """`function_outline` without changing the working folder (the caller holds `_in_folder`)."""
    data = content.encode("utf-8", "surrogateescape")
    try:
        tu = _get_index().parse(
            path,
            args=args,
            unsaved_files=[(path, content), *(others or [])],
            options=cindex.TranslationUnit.PARSE_INCOMPLETE,  # bodies are parsed: skipping them loses end lines
        )
    except cindex.TranslationUnitLoadError:
        return None
    target = os.path.realpath(path)
    out: dict = {"functions": [], "declarations": [], "variables": []}
    protos: dict[str, set[str]] = {}
    noproto: set[str] = set()
    real: dict[str, bool] = {}

    def is_target(name: str) -> bool:
        if name not in real:
            real[name] = os.path.realpath(name) == target
        return real[name]

    def visit(cursor: Cursor) -> None:
        position = 0
        for c in cursor.get_children():
            f = c.location.file
            if f is None:
                continue
            if c.kind == K.FIELD_DECL:
                position += 1
            if not is_target(f.name):
                if c.kind in FUNC_KINDS and not c.is_definition() and c.spelling:
                    qn = "::".join([*_scope_parts(c), c.spelling])
                    protos.setdefault(qn, set()).add(_outline_function(c, b"")["signature"])
                    if c.type.kind == TypeKind.FUNCTIONNOPROTO:
                        noproto.add(qn)
                elif c.kind in DESCEND_KINDS:
                    visit(c)
                continue
            if c.kind in FUNC_KINDS:
                entry = _outline_function(c, data)
                if (
                    c.is_definition()
                    and ref_lines
                    and any(entry["line_start"] <= n <= entry["line_end"] for n in ref_lines)
                ):
                    entry["refs"], entry["unresolved"] = _body_refs(c)
                    # an inactive branch is not in the tree: what it refers to can't be seen
                    text = data[c.extent.start.offset : c.extent.end.offset].decode(
                        "utf-8", "surrogateescape"
                    )
                    entry["conditional"] = _CONDITIONAL.search(text) is not None
                out["functions" if c.is_definition() else "declarations"].append(entry)
            elif c.kind == K.FIELD_DECL or (c.kind == K.VAR_DECL and _variable_kind(c) == "global"):
                if c.spelling:
                    kind = "field" if c.kind == K.FIELD_DECL else "global"
                    out["variables"].append(_outline_variable(c, kind, position - 1, data))
            elif c.kind in DESCEND_KINDS:
                visit(c)

    visit(tu.cursor)
    for key in ("functions", "declarations"):
        out[key].sort(key=lambda d: (d["line_start"], d["usr"]))
    out["variables"].sort(key=lambda d: (d["line"], d["qualified_name"]))
    errors = [d for d in tu.diagnostics if d.severity >= cindex.Diagnostic.Error]
    out["errors"] = len(errors)
    # warnings in clang that GCC 14+ reports as errors: a change that adds one may break the build there
    gcc_errors = [
        d
        for d in tu.diagnostics
        if d.severity == cindex.Diagnostic.Warning and d.spelling.startswith(GCC14_ERRORS)
    ]
    mine = sorted(
        {
            (d.location.line, d.spelling)
            for d in errors + gcc_errors
            if d.location.file and is_target(d.location.file.name)
        }
    )
    out["diagnostics"] = [{"line": n, "text": t} for n, t in mine]
    out["protos"] = {k: sorted(v) for k, v in sorted(protos.items())}
    out["noproto"] = sorted(noproto)
    return out
