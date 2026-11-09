# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `aerotrace tests [BASE | --patch FILE | --symbol FN]` and the `tests_for_change` MCP tool: which tests
  to run for a change, from test entry points in the index (GoogleTest, Unity, CMocka, test programs with
  `main`) linked through callers. Each test is exact or probable, with the reason. Changed code no test is
  known to reach is listed as "Not known", never as untested. Read-only; it never runs tests.
- `aerotrace tests record` and `aerotrace tests status`: run the project's tests once, when the user
  asks, with gcov, as `.aerotrace/tests.toml` says (shown in full, confirmed first), and save which test
  ran which function in `.aerotrace/tests.json` (a separate, versioned file; the index is unchanged).
  `aerotrace tests` then lists the recorded tests too, flags coverage recorded for other file contents or
  before test files changed, and lists code no recorded run executed (only as `probable`). Works with GNU
  gcov and LLVM gcov (`gcov` on macOS).
- `aerotrace signature <function>` and the `signature_impact` MCP tool: what a change to a function's
  parameters or return type affects. Every call site with its argument count and how it uses the return
  value (stored, compared, checked, passed on, returned, ignored, discarded), every place that uses the
  function's address (handler tables, callbacks, comparisons), pointer calls that may reach it, and callers
  further up. Each result is exact or probable, with the reason. Calls that no longer compile are listed.
- Index format 3 (ADR 0007): call sites, address uses, escapes (`foo(&x)`), designated initializers and
  "no member named" errors are recorded.

- Cross builds: the target comes from the compiler name (`arm-none-eabi-gcc` -> `--target=arm-none-eabi`),
  so ARM-only flags parse and target `#ifdef`s are right. The compiler is never run.
- GCC-only flags clang does not know no longer count as parse errors. If libclang refuses a file, it is
  parsed again without its `-m` target flags, and the answer says so.
- `@file` response files in compile commands are read (only inside the repo or the build folder).
- Answers now say when other `compile_commands.json` files were found, which source files the build does
  not list, and which headers were not found. `aerotrace doctor --build-dir` shows the same.
- Specific advice on how to get a `compile_commands.json` for CMake, Meson, Bazel, Make or Visual Studio.

### Fixed

- Linux: when the `clang` on PATH and the loaded libclang were different versions (GitHub's Ubuntu runner has
  clang 21 and libclang 22), clang 21's own headers were passed to libclang 22. Its `stdatomic.h` then
  defined nothing, so every C11 atomic (all of Redis's `atomicIncr`/`atomicGet`) was a parse error. AeroTrace
  now leaves out another compiler's builtin headers when libclang has its own.
- Writes through an address are now recorded: C11 atomics (`atomic_fetch_add_explicit(&x, ...)`,
  `atomic_store_explicit`), `__atomic_*` and `__sync_*` builtins, and standard C calls that write their first
  argument (`memset`, `memcpy`, `strcpy`, `snprintf`, ...). Atomic loads stay reads. Before, a function that
  only changed a counter atomically looked like it wrote nothing, so its readers were missing from impact.
  A pointer passed to any other function (`foo(&x)`) is still not counted, and every answer says so.
- Re-indexing (by the MCP server or `aerotrace index`) now keeps the `--build-dir` and `--exclude` the
  index was built with. Before, it fell back to guessed flags when the database was outside the repo.
- The index is rebuilt when `compile_commands.json` changes, not only when source files change.

- `aerotrace diff [BASE]` and the `diff_impact` MCP tool: what a whole change could break. Compares the
  working tree (uncommitted and new files included) with a commit, or with where the branch left `main`.
  `--patch FILE` checks a change that is not applied yet, after making sure it matches the files on disk.
- Diff impact lists changed functions, functions the diff removed (found by parsing the old version from
  git) and who still calls them, changed globals and fields with every user, then all affected callers.
  Prototype and `extern` changes in headers are matched to their definitions. Lines it can't follow
  (macros, types, files not in the index) are listed, never dropped.

- MCP server (`aerotrace mcp`) with `impact`, `callers`, `callees`, `find` and `index_status` tools.
  It builds the index on the first question and re-indexes changed files before each answer.
- `aerotrace install --client claude|cursor|codex` adds the server to Claude Code (`.mcp.json`),
  Cursor (`.cursor/mcp.json`) or Codex (`~/.codex/config.toml`, or `$CODEX_HOME`), keeping other settings.

- `aerotrace callers`, `callees` and `impact` (blast radius): transitive, with a depth limit, nearest first,
  capped output, and a confidence label on every result. Impact also lists functions that read data the
  changed function writes, and ends with the limits of the answer.
- `aerotrace find`: by name, `ns::Class::method`, `name@file` or `file.c:LINE`. A name that matches several
  functions returns all of them; we never pick one silently.
- Function-pointer pass: a call through a pointer of type T is linked (`via_pointer`) to every function of
  type T whose address is taken. On Redis this links `call()` to all 323 command handlers.

- `aerotrace index`: parses C and C++ with libclang and stores calls, overrides, reads and writes of
  globals, struct fields and static locals in `.aerotrace/index.db`.
- Uses the project's `compile_commands.json` (repo root, `build*/`, `cmake-build-*/`, `out/*/` or `--build-dir`).
  Without one, it guesses flags, parsing C as C and C++ as C++.
- Collision-safe IDs: two `main()`s, or two `static` functions in two files with the same name, stay apart.
  References a file can't resolve to one definition are marked `ambiguous`.
- Blind spots are recorded: calls through pointers, and calls to functions with no parsed definition.
- Functions whose address is taken (callback tables) are recorded, for the function-pointer pass.
- `aerotrace doctor` (libclang, flags, headers) and `aerotrace status` (counts, files changed since indexing).
- The system libclang is preferred over the bundled one; its version is part of the cache key.
- Flags that would load compiler plugins are removed from compile commands.
- Repo rules, contribution guide, CI, templates and the first decision records.
