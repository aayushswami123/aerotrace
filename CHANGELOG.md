# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - not released yet

The first release. AeroTrace tells an AI coding agent (or you) what a change to C or C++ code will
affect, before the edit: callers, callees, callers further up, and the globals and struct fields the code
reads and writes. It runs on your machine, reads your `compile_commands.json`, never runs your build and
sends nothing anywhere. The same code, flags and version always give the same answer.

### What it does

- **`aerotrace check`**: the main question. For a change (your working tree, a commit, or a patch that is
  not applied yet) it says what will not build, what may change behaviour, and what it could not follow.
  The first line gives the verdict with counts. Exit code 1 when something breaks, 2 when it could not
  check. `--html` writes the same answer as one offline HTML file.
- **Questions about one function or field**: `callers`, `callees`, `impact` (what could break if this
  function changes), `signature` (what a change to its parameters or return type affects, call site by
  call site), `data` (who reads and writes a field or global, before you rename, remove or retype it),
  `find`, and `diff` (the impact of a whole change).
- **`aerotrace tests`**: which tests to run for a change and why. It reads tests from the index
  (GoogleTest, Unity, CMocka, test programs). It never runs them, and code no test is known to reach is
  listed as "not known", never as untested.
- **For agents**: an MCP server (`aerotrace mcp`) with the same questions, `aerotrace install` for Claude
  Code, Cursor and Codex, and an optional Claude Code edit check that warns before an edit that breaks
  callers.
- **Every answer says how sure it is and what it could not see.** Each result is `exact` or `probable`
  (or `unknown`), with the reason, and the answer ends with its blind spots and the limits of the index.
  Lists are short and say how many were left out.
- Works on Linux and macOS (tested in CI with Python 3.11 and 3.13), with C and C++, including cross
  builds (ARM and other targets). Checked against Redis 8.10.3 and SQLite 3.54.1 with hand-checked facts.

### Main fixes before this release

- Calls that don't compile (right after you change a function's parameters) are still found, in C and
  C++, including constructors and `operator()` calls (#14, #21, #39).
- A removed field or global still used in a file-scope table or initializer is reported as a break (#57),
  and many more uses of fields and globals are seen: fields set by position, addresses stored in tables,
  `x++`, `offsetof`, and every compile error in a file instead of the first 20 (#18, #19, #24 to #27, #29,
  #37, #52).
- C++: template calls that can't be linked say so instead of looking complete (#33); overrides that stop
  overriding keep their callers (#32); header functions compiled as C and C++ can be told apart (#40).
- Comparing a function's address is no longer taken for a use of it (#12); `(*f)(x)` is a direct call (#22).
- `check`: a changed function that no longer builds stays in "What changed" (#49); CRLF files work with
  `--patch` (#51); a pass names the files outside the build right below its headline.
- The index: files saved during a build are re-indexed (#43); two parses at once in the MCP server no
  longer use each other's folder (#46); C11 atomics parse on Linux.

### Known limits

- **One build configuration.** Code under other `#ifdef` settings, and files not in
  `compile_commands.json`, are not analysed. Every answer says how many files were left out.
- **Calls through function pointers** are matched by type: linked to every function of that type whose
  address is taken, marked `probable`. Virtual calls are `probable` too.
- **Macros**: uses are reported at the line where the macro is used; names built with `##` only where it
  is expanded in this build.
- **C++ templates**: calls whose target depends on the template type can't be linked (they are listed as
  unknown). Inherited constructors in broken calls are not seen.
- **Data**: fields are tracked per struct type, not per object. A pointer passed to another function
  (`foo(&x)`) is not followed. Access through `void *`, casts, or raw bytes is not seen.
- **Outside the repo**: callers in other programs or libraries that link your code are not visible.
- **Open issues**: a class named `function` outside `std` is taken for `std::function` (#69); a relative
  `directory` in `compile_commands.json` (#72); two file-scope things on one line (#73); C++ file-scope
  dynamic initializers (#28); results stored in a local variable are not followed by `signature` (#16).
- Needs libclang (the system one, or the bundled Python package). No Windows testing yet. Only C and
  C++ (Ada later).

### Added (in detail)

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

### Fixed (in detail)

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
