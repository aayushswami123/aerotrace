# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `aerotrace check`: when something breaks, the first line now ends with the counts in the words the
  lists use, e.g. `(4 BREAKS, 19 AFFECTED)`. A coverage line comes before the proof line: `Checked 117
  of 117 files in this build (1 had parse errors).`, plus one short line for repo sources outside
  compile_commands.json, naming up to 3 of them (`1,696 C/C++ files outside this build were not checked:
  deps/a.c, deps/b.c, deps/c.c and 1,693 more`); under a "nothing found" headline it comes right below it. It replaces the long
  "not in compile_commands.json" limit. New `aerotrace check --all`: no caps, and lists those files. On a
  terminal, long lines wrap at word boundaries to the terminal width; pipes, `--json` and MCP answers are
  not wrapped. The JSON `proof` has new optional counts (`build_files`, `build_files_checked`,
  `build_files_with_errors`, `outside_build`, `outside_build_examples`).
- Claude Code edit check: `aerotrace install --client claude` now also adds a `PreToolUse` hook (in the
  repo's `.claude/settings.json`, other settings kept). Before Claude edits a C/C++ file, the edit is
  checked as a patch: exact breaks give the full list (functions, data, tests to run), probable breaks or
  10+ affected give a short warning, anything else is silent. It warns only, unless
  `.aerotrace/rules.toml` has a `block` rule. It never blocks on its own failure (missing or stale index,
  errors, over 5 s). `aerotrace hook on|off|status`.
- MCP server: while it builds or rebuilds the index it sends progress (files done out of total). A build
  that takes more than 20 s keeps going in the background and the agent is told "still building: N of M
  files done, ask again shortly". A failed build says why (no compile_commands.json in `--build-dir`,
  libclang missing, a parser crash, disk full, ...) instead of "Error executing tool". Quitting the server
  mid-build ends the parser processes at once. New `aerotrace mcp --jobs N`.
- `aerotrace data <Struct::field | global | name@file | global.field>` and the `data_users` MCP tool:
  before renaming, removing or retyping a field or global, every function that reads or writes it (with
  lines), initializers that set it, code that still uses a removed field, globals of its type that may set
  it by position, and where its address goes (unknown). For a global, also the callers of its users.
  `--from FUNCTION` asks who reads what that function writes (a field's readers are probable: fields are
  tracked per struct type, not per object). Every answer states its limits.
- `aerotrace tests [BASE | --patch FILE | --symbol FN]` and the `tests_for_change` MCP tool: which tests
  to run for a change, from test entry points in the index (GoogleTest, Unity, CMocka, test programs with
  `main`) linked through callers. Each test is exact or probable, with the reason. Changed code no test is
  known to reach is listed as "Not known", never as untested. Read-only; it never runs tests.
- `aerotrace signature <function>` and the `signature_impact` MCP tool: what a change to a function's
  parameters or return type affects. Every call site with its argument count and how it uses the return
  value (stored, compared, checked, passed on, returned, ignored, discarded), every place that uses the
  function's address (handler tables, callbacks, comparisons), pointer calls that may reach it, and callers
  further up. Each result is exact or probable, with the reason. Calls that no longer compile are listed.
- `signature` answers are ranked and short: a summary first (counts, exact/probable split, calls that do
  not compile, every file with its count), then call sites that do not compile, then the ones that use the
  result, then the ones that ignore it. Callers further up are grouped by the caller they come through
  (one group per pointer call). Text lists are capped (about 15) and say how many were left out;
  `--all` (MCP `show_all=true`) shows everything, `--limit N` still works. JSON keeps its order and
  only gains keys (`summary`, `upstream_groups`, `rank_group`, `*_omitted`, `not_compiling_call_sites`).
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

- `aerotrace check --patch`: a function removed together with its header prototype, or removed in a
  shifted hunk (`diff -u`, the Claude Code hook), is now REMOVED and its callers BREAK, like the same change
  written by git. A prototype removed while the function stays (or moves) makes callers in other files MAY BREAK
  when compiling them with the patch fails, and gets a limit line. A function moved to a new file is MAY BREAK
  (not sure) with a limit line: the new file is not in the build list yet.
  Files that only take the function's address are compiled too. A new file only downgrades BREAKS when it
  defines the same signature (not another overload, not in an unnamed namespace, not static).
- Four wrong "safe" answers found by the benchmark (redis-05, curl-05, redis-07, curl-07), where the build
  failed but `check` said "None is sure to break":
  - A removed function still named in a table (a command table, a filter's callbacks) is now a break of
    that table: it no longer compiles (static) or no longer links (nothing else defines it).
  - A compile error outside every function in a patched file is a break of the global whose initializer
    holds it.
  - A field or global whose type really changes (for example an integer to `void *`): the files of its
    users are compiled with the change, and a use that no longer builds is a break. Uses that still build
    are affected; uses in files that were not compiled (more than 8 files) are unknown, never "affected".
    Errors that only newer compilers give (int-conversion, incompatible function pointers, and clang
    warnings that GCC 14+ makes errors, such as incompatible pointer types) are probable breaks. A file
    that already fails to build before the change gives unknown, not "still compiles". An initializer
    that sets the field is compiled too.
  - A function the same patch removes and defines again in another file (moved) is not a break.
  - Git mode: a removed function leaves a blind spot (a table or pointer in another file that names it
    only fails at link time and is not listed), so the answer is never an all-clear.
  - The headline says "None is sure to break" only when nothing is unknown and every part of the change
    was followed.
- `check` without `--patch` (the index already has the change): a removed field or global still used in a
  file-scope initializer (`{"hz", &server.hz}` in a table, `{.hz = 7}`) is now a break of that global.
  It is exact only when that global is the only one on the line and the index shows its initializer
  there; otherwise the globals that may hold it are probable breaks and the line also stays a blind spot.
  Before, it was only a blind-spot line (with "does not compile now" twice) and the headline said
  "unknown" (#57).
- Two parses inside one process at the same time (for example two `diff_impact` or `tests_for_change`
  calls to the MCP server) could each run in the other's build folder, so relative `-I` paths and
  includes could resolve in the wrong place and an outline could be wrong or a header reported missing.
  In-process parses now take turns (#46). Index builds run in child processes and are not affected.
- C++ templates: a call whose target depends on the template type (`t.run(5)`, `T::f()`, `f(t)`) can't be
  linked, because libclang does not show the instantiations. `callers`, `impact`, `signature`, `diff` and
  `check` for a function with that name now say "UNKNOWN (#33)" and list the template calls, instead of
  looking complete. An overload set used as a value in a template (`T p = ov;`) takes the address of every
  candidate (#31).
- `diff`/`check` in git mode: when a change makes a virtual override stop overriding (new parameters), the
  callers that dispatched to it through the base are listed again, as `virtual` (#32).
- `check`: a changed function that no longer builds stays in "What changed" (distance 0), and its break is a
  separate node, still counted (#49).
- `check --patch` on CRLF files: the patch is read as it is (no line-end change), and an LF patch fits a
  CRLF file too, so the changed function is found (#51).
- A header function compiled as C and as C++: `file:line` now gives both copies, and the choices show the
  clang ID and the language, so each can be asked for (#40).
- Comparing a function's address (`proc == f`, `if (f)`) no longer counts as taking it (#12). `(*f)(x)` and
  `(&f)(x)` are direct calls to `f` (#22).
- `aerotrace data` and `aerotrace check` see more uses of fields and globals: `&x` stored in a file-scope
  table (#27), fields set by position (`{1, 2}`, #26), `x++` and `x += n` as a read and a write (#52),
  `{.f = 1}` inside functions (#37), `offsetof` (#24), removed fields in `{.f = ...}` and removed globals
  (#25), all compile errors in a file instead of the first 20 (#29). An array element passed by value is no
  longer an escape (#18). Unnamed structs and unions no longer have a machine path in their names (#19).
  The index is rebuilt once after the upgrade.
- A file saved while the index was being built is re-indexed next time. Before, it could look unchanged
  forever and answers missed its new callers (#43). Parse caches are rebuilt once after the upgrade.
- A call that doesn't compile (wrong number or type of arguments) is a call again: its caller shows up in
  `callers`, `impact` and `diff`, and the function no longer counts as "address taken" (#14). Right after a
  parameter is added in the working tree, every old caller is such a call. This covers C, and in C++ calls to
  functions, templates, methods (`k.m(1)`, `p->m(1)`, virtual ones too) and static members. When no overload
  matches, the call is linked to every function of that name, marked `ambiguous`. A bad call through a
  function pointer, a `std::function` or a pointer to member is still a pointer call. A function used as a
  value where it does not fit (`int x = f;`) is not taken for a call. C++ constructor calls (`K k(1, 2)`,
  `new K(1, 2)`, #21) and calls on an object with `operator()` (#39) are found from the compile error and
  linked to every constructor (or `operator()`) of that class in the repo; an inherited one is not seen, and
  the call is then listed as a blind spot.
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
