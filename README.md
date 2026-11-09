# AeroTrace

A safety layer for AI coding agents working on critical, legacy C and C++ code.
Before an agent edits code, AeroTrace shows what the change will affect. It runs fully on your machine.

Status: early development.

## Install

```bash
uv tool install git+https://github.com/aayushswami123/aerotrace   # or: pipx install git+...
```

## Use it with your coding agent

Run one command inside your C/C++ repo:

```bash
aerotrace install --client claude   # writes .mcp.json (Claude Code)
aerotrace install --client cursor   # writes .cursor/mcp.json (Cursor)
aerotrace install --client codex    # adds it to ~/.codex/config.toml (Codex, every repo)
```

Add `--dry-run` to see the change first. Only the `aerotrace` entry is added or replaced; other servers are kept.

The agent gets these tools: `impact`, `diff_impact`, `signature_impact`, `data_users`, `tests_for_change`,
`callers`, `callees`, `find` and `index_status`. The first question
builds the index. After that, files you edit are re-indexed before each answer.

## Use it from the terminal

```bash
aerotrace doctor               # checks libclang and finds compile_commands.json
aerotrace index                # builds .aerotrace/index.db
aerotrace impact setCommand    # what could break if this function changes
aerotrace callers dictAdd --depth 2
aerotrace signature lookupKeyReadOrReply   # every call site, and how each uses the return value
aerotrace signature addReply --all         # a summary and the top call sites by default; --all for every one
aerotrace data redisServer::hz   # every function that reads or writes a struct field or global
aerotrace find src/server.c:4221
aerotrace diff                 # what your uncommitted edits could break (new files included)
aerotrace diff main            # everything on this branch, compared with where it left main
aerotrace diff --patch fix.diff   # a change that is not applied yet
aerotrace tests                # which tests to run for your edits (same inputs as diff, or --symbol FN)
```

`tests` finds C/C++ test entry points in the index (GoogleTest `TEST`/`TEST_F` and fixture `SetUp`,
Unity `RUN_TEST`, CMocka `cmocka_unit_test`, and `main` in test files) and links them to the changed code
through callers. Each test is `exact` (only direct calls) or `probable` (a pointer, virtual or data link),
with the reason. Changed code no C/C++ test calls is listed as "Not known", never as untested: tests
written as scripts (such as Redis's and SQLite's Tcl tests) are not seen by static links.

`diff` maps changed lines to functions, globals and fields, then lists everything upstream of them.
To see which functions a diff *removed*, it reads the old version of each changed file from git, so a
deleted function and the code that still calls it are reported. Changes it can't follow (macros, types,
files outside the index) are listed at the end, never dropped.

### Compile flags

For accurate results, give AeroTrace your build's `compile_commands.json`
(CMake: `-DCMAKE_EXPORT_COMPILE_COMMANDS=ON`; Make: run the build under [`bear`](https://github.com/rizsotto/Bear)).
AeroTrace looks in the repo root, `build*/`, `cmake-build-*/` and `out/*/`, or use `--build-dir`. It remembers
the folder, so later re-indexing (also by the MCP server) uses the same flags. When the file changes, the
index is rebuilt.

It handles what real builds contain:

- **Cross compilers.** `arm-none-eabi-gcc` means AeroTrace parses for `arm-none-eabi`, so `-mcpu=...` works
  and target `#ifdef`s match the real build. The compiler is never run; only its name is read.
- **GCC-only flags** that clang does not know are ignored and listed, not counted as errors.
- **Response files** (`@flags.rsp`) are read when they are inside the repo or the build folder.

`aerotrace doctor` shows which database is used, other databases it found, the target, and source files
the build does not list. Every answer's limits mention files left out, headers that were not found, and
other databases, so a wrong setup is never silent.

## What the labels mean

Every link has a confidence label, and every impact answer ends with its limits.

| Label | Meaning |
|---|---|
| (none) | A direct call the compiler resolved |
| `virtual` | A virtual call that may land on this override |
| `via_pointer` | A call through a function pointer whose type matches |
| `ambiguous` | Several functions share this name; this is one of them |
| `data` | Reads a global or field the changed function writes |
| `uses_changed` | Reads or writes a global or field whose declaration changed (`diff` only) |
| `calls_removed` | Still calls a function the change removes; it will not build or link (`diff` only) |

Licensed under the Apache License 2.0. See [LICENSE](LICENSE).
