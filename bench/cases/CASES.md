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

| id | kind | level | symbol | where | counts (regex) | why this case |
|---|---|---|---|---|---|---|
| redis-01 | add_param | easy | `src/networking.c:_addReplyToBufferOrList` | `src/networking.c:514` | calls 18, live calls 18, mentions 19 | Every direct caller stops compiling; drawn from functions with 10 to 150 direct calls (wide fan-out). |
| redis-02 | remove_fn | easy | `src/bitops.c:redisPopCountAarch64` | `src/bitops.c:168` | calls 2, live calls 1, mentions 3 | Callers fail to compile, or, when a prototype stays in a header, only at link. |
| redis-03 | int_to_void | easy | `src/bitops.c:checkSignedBitfieldOverflow` | `src/bitops.c:616` | calls 2, live calls 2, mentions 3 | Only callers that use the value break; callers that ignore it still build. |
| redis-04 | ptr_param | hard | `src/server.c:dictSdsKeyCompare` | `src/server.c:370` | calls 2, live calls 2, values 15, live values 15, mentions 19 | Direct calls break, and so does the place it is put in a table or assigned to a pointer. |
| redis-05 | ptr_remove | hard | `src/db.c:delexGetKeys` | `src/db.c:3964` | values 1, live values 1, mentions 3 | Nothing calls it by name; only the table or assignment that names it breaks. |
| redis-06 | field_rename | hard | `redisServer.aof_dirname` | `src/server.h` | uses 45, live uses 38 | Every reader and writer breaks; same-named fields of other structs must not be counted. |
| redis-07 | field_type | hard | `redisServer.stat_current_cow_peak` | `src/server.h` | uses 9, live uses 8 | Arithmetic and integer uses break; some uses still build and may behave differently. Type `size_t`. |
| redis-08 | global_rename | hard | `src/bitops.c:bitsinbyte` | `src/bitops.c:53` | uses 8, live uses 4 | Every use breaks (at compile time, or at link when an `extern` stays). |
| redis-09 | macro_arity | hard | `src/zmalloc.c:MALLOC_MIN_SIZE` | `src/zmalloc.c:56` | uses 6, live uses 2, defines 1, live defines 1 | Every use breaks; macros are a common gap for tools that read the code after the preprocessor. |
| redis-10 | behaviour | might_break | `src/fast_float_strtod.c:read8_to_u64` | `src/fast_float_strtod.c:320` | calls 2, live calls 2, mentions 3 | Nothing fails to build; callers that use the value may behave differently (truth checked by hand). |
| sqlite-01 | add_param | easy | `shell.c:rePeek` | `shell.c:9516` | calls 10, live calls 10, mentions 11 | Every direct caller stops compiling; drawn from functions with 10 to 150 direct calls (wide fan-out). |
| sqlite-02 | remove_fn | easy | `sqlite3.c:jsonBlobAppendOneByte` | `sqlite3.c:214984` | calls 4, live calls 4, mentions 5 | Callers fail to compile, or, when a prototype stays in a header, only at link. |
| sqlite-03 | int_to_void | easy | `sqlite3.c:pagerIsSuperJrnlName` | `sqlite3.c:60257` | calls 3, live calls 3, mentions 4 | Only callers that use the value break; callers that ignore it still build. |
| sqlite-04 | ptr_param | hard | `sqlite3.c:sqlite3_column_int` | `sqlite3.c:94717` | calls 101, live calls 30, values 1, live values 1, mentions 106 | Direct calls break, and so does the place it is put in a table or assigned to a pointer. |
| sqlite-05 | ptr_remove | hard | `shell.c:vfstraceDelete` | `shell.c:19506` | values 1, live values 1, mentions 3 | Nothing calls it by name; only the table or assignment that names it breaks. |
| sqlite-06 | field_rename | hard | `sqlite3.pSavepoint` | `sqlite3.c` | uses 14, live uses 13 | Every reader and writer breaks; same-named fields of other structs must not be counted. |
| sqlite-07 | field_type | hard | `Parse.eOrconf` | `sqlite3.c` | uses 3, live uses 3 | Arithmetic and integer uses break; some uses still build and may behave differently. Type `u8`. |
| sqlite-08 | global_rename | hard | `shell.c:shellCmdLineEdit` | `shell.c:238` | uses 3, live uses 2 | Every use breaks (at compile time, or at link when an `extern` stays). |
| sqlite-09 | macro_arity | hard | `sqlite3.c:AtomicLoad` | `sqlite3.c:15115` | uses 20, live uses 20, defines 2, live defines 1 | Every use breaks; macros are a common gap for tools that read the code after the preprocessor. |
| sqlite-10 | behaviour | might_break | `sqlite3.c:sqlite3BtreeCursor` | `sqlite3.c:77526` | calls 4, live calls 4, mentions 6 | Nothing fails to build; callers that use the value may behave differently (truth checked by hand). |
| curl-01 | add_param | easy | `lib/dynhds.c:Curl_dynhds_add` | `lib/dynhds.c:134` | calls 13, live calls 9, mentions 15 | Every direct caller stops compiling; drawn from functions with 10 to 150 direct calls (wide fan-out). |
| curl-02 | remove_fn | easy | `src/tool_paramhlp.c:add2list` | `src/tool_paramhlp.c:601` | calls 15, live calls 15, mentions 17 | Callers fail to compile, or, when a prototype stays in a header, only at link. |
| curl-03 | int_to_void | easy | `lib/cf-socket.c:socket_close` | `lib/cf-socket.c:535` | calls 5, live calls 5, mentions 6 | Only callers that use the value break; callers that ignore it still build. |
| curl-04 | ptr_param | hard | `lib/cw-pause.c:cw_pause_flush` | `lib/cw-pause.c:102` | calls 1, live calls 1, values 1, live values 1, mentions 3 | Direct calls break, and so does the place it is put in a table or assigned to a pointer. |
| curl-05 | ptr_remove | hard | `lib/http_proxy.c:http_proxy_cf_destroy` | `lib/http_proxy.c:696` | values 1, live values 1, mentions 2 | Nothing calls it by name; only the table or assignment that names it breaks. |
| curl-06 | field_rename | hard | `UserDefined.http_follow_mode` | `lib/urldata.h` | uses 5, live uses 5 | Every reader and writer breaks; same-named fields of other structs must not be counted. |
| curl-07 | field_type | hard | `UserDefined.upkeep_interval_ms` | `lib/urldata.h` | uses 7, live uses 7 | Arithmetic and integer uses break; some uses still build and may behave differently. Type `timediff_t`. |
| curl-08 | global_rename | hard | `src/tool_libinfo.c:feature_altsvc` | `src/tool_libinfo.c:67` | uses 3, live uses 3 | Every use breaks (at compile time, or at link when an `extern` stays). |
| curl-09 | macro_arity | hard | `lib/curl_trc.h:Curl_trc_is_verbose` | `lib/curl_trc.h:321` | uses 14, live uses 4, defines 2, live defines 1 | Every use breaks; macros are a common gap for tools that read the code after the preprocessor. |
| curl-10 | behaviour | might_break | `lib/vauth/vauth.c:Curl_auth_allowed_to_origin` | `lib/vauth/vauth.c:145` | calls 2, live calls 2, mentions 4 | Nothing fails to build; callers that use the value may behave differently (truth checked by hand). |

## Kinds of change

| kind | level | the edit |
|---|---|---|
| add_param | easy | add a trailing `int aerotrace_bench` parameter to the definition and every prototype; calls unchanged |
| remove_fn | easy | delete the definition; any prototype stays |
| int_to_void | easy | change the return type from int to void in the definition and every prototype; `return X;` becomes `(void)(X); return;` |
| ptr_param | hard | add a trailing `int aerotrace_bench` parameter to a function that is called directly and also stored as a pointer |
| ptr_remove | hard | delete the definition of a function that is reached only through a pointer; any prototype stays |
| field_rename | hard | rename the struct field to `<name>_renamed` in the struct only |
| field_type | hard | change the type of an integer struct field to `void *` |
| global_rename | hard | rename the definition of a file-scope variable to `<name>_renamed`; any `extern` stays |
| macro_arity | hard | add a trailing parameter `aerotrace_bench` to every `#define` of the function-like macro |
| behaviour | might_break | make the function return the zero value of its type (0, NULL or false) as its first statement |

## Pool sizes and skips

- redis: 5296 function definitions, 659 fields, 270 compiled file-scope variables, 328 function-like macros. 42 candidates skipped (all in `cases.json`, with reasons).
- sqlite: 5641 function definitions, 228 fields, 86 compiled file-scope variables, 755 function-like macros. 91 candidates skipped (all in `cases.json`, with reasons).
- curl: 4284 function definitions, 196 fields, 231 compiled file-scope variables, 570 function-like macros. 296 candidates skipped (all in `cases.json`, with reasons).
