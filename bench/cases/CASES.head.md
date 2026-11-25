# Benchmark cases (Phase 1, step 1)

Status: **drawn, not run.** No tool (AeroTrace, clangd, cscope, ripgrep) has been run on any case. This
list is committed first so it can't be tuned to the results. The owner approves it before anything runs.

Plan: `docs/plans/dashboard-and-benchmark.md` section 7 (PR #62).

## Draw 1 was withdrawn

A review of this PR found bugs in the first draw. Nothing had been run on it. It is kept as
`withdrawn-draw-1.json`. The bugs:

- Redis's command table (`src/commands.def`) and curl's subfolders (`lib/vtls`, `lib/vauth`, ...) were
  not scanned, so counts were wrong there.
- The `ptr_param` filter accepted anything.
- Counts included member calls (`->name(`), a function calling itself, and same-named declarations
  (`bool xattr;`).
- A macro that is also a function name was taken for a macro.
- curl had no fixed build, so "compiled" wasn't defined for it.

`pick.py` was fixed and the cases redrawn with **the same seed**. Changing the seed would have allowed
picking a draw we liked.

## What a case is

A small, realistic edit to real code in a pinned release: Redis 8.10.3, SQLite 3.54.1 (amalgamation) and
curl 8.23.0. The edits are **made by us**, not taken from the projects' history. Each kind of edit is one
an agent really makes (change a signature, remove a function, rename a field...), applied to a symbol
drawn by a fixed seed.

- curl 8.23.0: `https://curl.se/download/curl-8.23.0.tar.gz`, sha256
  `e3de77cfa6367083c776a26647fc9f633b538c06c3baa73d931a252ac8f72b49`. curl.se publishes PGP signatures,
  not hashes. gpg isn't installed here, so the hash was checked against the `digest` of the same asset
  on the GitHub release `curl-8_23_0` (they match).
- Redis and SQLite: as pinned in `tests/realrepo/repos.py`.

## The build each repo is judged on

"Breaks" means "the compiler or the linker reports an error" for this build, on this machine:

- **Compiler:** Apple clang 21.0.0 (clang-2100.1.1.101), arm64, macOS. Only errors count. No `-Werror`
  is added, and the projects' own warning flags are kept. Clang treats incompatible function-pointer
  types and int-to-pointer conversions as errors; older gcc does not. So the truth belongs to this
  compiler.
- **Redis:** the `redis-server` program (`REDIS_SERVER_OBJ` in `src/Makefile`), flags from
  `tests/realrepo/repos.py` (`-DREDIS_STATIC=`, `MALLOC=libc`). `redis-cli` and `redis-benchmark` are not
  part of it.
- **SQLite:** the `sqlite3` shell, `shell.c` and `sqlite3.c` with default options.
- **curl:** the `curl` program and the libcurl it links, configured by `bench/cases/curl_build.py` with a
  fixed, minimal option set that doesn't depend on what's installed: no TLS, HTTP/2, HTTP/3, compression,
  SSH or LDAP. So TLS, HTTP/2 and NTLM code is not part of the benchmark.

## How the cases were drawn (`bench/cases/pick.py`)

1. The 10 kinds of change are fixed for every repo (table at the end): 3 easy, 6 hard, 1 "might break".
2. Candidates come from plain regex scans of the source (`bench/accuracy/cfuncs.py`) and the C
   preprocessor. AeroTrace is never used to find or filter them.
3. Candidates are ordered by `sha256("aerotrace-bench-cases-1/" + repo + "/" + kind + "/" + id)`. The
   first one that passes the kind's filter is taken. A name is used by at most one case per repo.
4. **Compiled only.** A definition, use or `#define` counts only if its line survives `cc -E` (or
   `cc -E -dD` for `#define`s) with the build's flags. Every case has at least one compiled use.
5. Filters:
   - At most 150 mentions, so the truth stays checkable. "Might break" cases allow at most 40, because
     they are checked by hand.
   - `add_param`: 10 to 150 direct calls (wide fan-out).
   - `remove_fn`, `int_to_void`, `behaviour`: 2 to 30 direct calls, and no use as a value.
   - `ptr_param`: called directly **and** stored as a value.
   - `ptr_remove`: stored as a value, never called by name.
   - A "value" is a use in an assignment, argument, return or table initializer (including
     `commands.def`), not a declaration.
   - `int_to_void` returns `int`. `field_type` is a plain integer, not a bitfield. A macro may not share
     its name with a function.
6. Arm64-only code can be drawn, because "compiled" was checked on this machine (redis-02 is). The
   benchmark runs on this machine only.

Reproduce: unpack the three pinned archives into empty folders, run
`python3 -I bench/cases/curl_build.py configure <curl>`, then
`python3 -I bench/cases/pick.py <redis> <sqlite> <curl> > bench/cases/cases.json` and
`python3 -I bench/cases/render.py > bench/cases/CASES.md`. Two runs give a byte-identical
`cases.json` (about 2.5 minutes, most of it `cc -E`).

## Edits, exactly

Only what the kind says changes. Every other declaration (prototypes, `extern`s) stays as it is, so some
breaks show only at link time. The exact edit for each kind is in the table at the end. `behaviour`
returns the zero value of the type (`0`, `NULL` or `false`) as the first statement. Its truth is the
direct callers in compiled code that use the returned value, checked by hand with the lines that show it.

## Rules fixed now, before the run

- **Edit can't be applied** as written (for example the function body is a macro), or **the edited code
  isn't compiled** after all: the case is replaced by the next candidate in seed order, and the
  replacement is logged in `cases.json` with its reason.
- **Macro variants.** When a macro has several `#define`s under `#if`, the edit is made to all of them.
  Only the compiled one matters for the truth.
- **A case with no breaks stays in.** For example, an `int_to_void` whose callers all ignore the value
  scores false alarms only.

## Known slant, said up front

- `macro_arity` is a common gap for tools that read the code after the preprocessor. `remove_fn` and
  `global_rename` may only fail at link time when a prototype or `extern` stays. How AeroTrace does on
  these hasn't been checked; they stay in either way.
- Most cases have few uses (often 2 to 20). That suits grep, which works well on small, unique names.
  `add_param` is drawn from wide fan-outs (10 to 150 calls) to balance this a little.
- The counts in the table come from regex, which can still miss calls hidden inside other macros. They
  only decide which cases are drawn. The truth comes from the compiler and linker.

## Cases
