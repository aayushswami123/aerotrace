# 0001: Build the core in Python on libclang

- Status: accepted
- Date: 2026-11-08

## Context

We need accurate C/C++ facts (calls, types, reads, writes) from real compiler flags. The options were
Python with libclang, C++ with Clang LibTooling, or Rust with libclang. We are one person with a 3-week plan.
The hackathon parser was already Python + libclang, and it worked well on Redis once given a compile database.

## Decision

Write the core in Python 3.11+ using the libclang C API (`clang.cindex`). Prefer the libclang already on the
machine (Xcode, LLVM install, or `AEROTRACE_LIBCLANG`), and fall back to the one bundled in the `libclang`
wheel. Record the libclang version in the index, and include it in every cache key.

## Consequences

- Fast to build, and easy to install with `uv` / `pipx`.
- libclang hides some of the AST (for example implicit calls). If we hit a hard wall, we can move the
  parser alone to LibTooling. The schema and analysis layers don't depend on how facts are made.
- The bundled wheel is stuck at clang 18, which fails on new macOS SDK headers. That is why we look for
  the system libclang first.
- Answers can differ between libclang versions. `aerotrace doctor` and the index status show the version.
