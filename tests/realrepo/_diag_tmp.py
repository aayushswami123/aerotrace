import os, subprocess, shutil, tempfile
from aerotrace.toolchain import libclang
ch = libclang.find_libclang(); libclang.configure(ch); print("libclang", ch.source, ch.path, libclang.clang_version())
for cc in ("clang", "gcc", "cc"): print(cc, shutil.which(cc))
from aerotrace.toolchain.compdb import system_include_args
inc = system_include_args("c"); print("system includes", inc)
from clang import cindex
d = tempfile.mkdtemp(); p = os.path.join(d, "a.c")
open(p, "w").write("#include <stdatomic.h>\nstruct S { _Atomic long long n; } s;\nvoid f(void){ atomic_fetch_add_explicit(&s.n, 1, memory_order_relaxed); }\n")
tu = cindex.Index.create().parse(p, args=["-x", "c", "-std=gnu11", *inc])
print("diags", [x.spelling for x in tu.diagnostics])
for i in tu.get_includes():
    if "stdatomic" in i.include.name: print("stdatomic from", i.include.name)
def walk(c, dd=0):
    if c.location.file and c.location.file.name == p and c.location.line == 3:
        print("  " * dd + f"{c.kind.name} {c.spelling!r} children={len(list(c.get_children()))}")
    for x in c.get_children(): walk(x, dd + 1)
walk(tu.cursor)
