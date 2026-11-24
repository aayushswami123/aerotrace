# Benchmark cases (Phase 1, step 1)

Status: **drawn, not run.** No tool (AeroTrace, clangd, cscope, ripgrep) has been run on any case. This
list is committed first so it can't be tuned to the results. The owner approves it before anything runs.

Plan: `docs/plans/dashboard-and-benchmark.md` section 7 (PR #62).

## What a case is

A small, realistic edit to real code in a pinned release: Redis 8.10.3, SQLite 3.54.1 (amalgamation) and
curl 8.23.0. The edits are **made by us**, not taken from the projects' history. Each kind of edit is
one an agent really makes (change a signature, remove a function, rename a field...), applied to a
symbol drawn by a fixed seed.

- curl 8.23.0: `https://curl.se/download/curl-8.23.0.tar.gz`, sha256
  `e3de77cfa6367083c776a26647fc9f633b538c06c3baa73d931a252ac8f72b49`. curl.se publishes PGP signatures,
  not hashes. gpg isn't installed here, so the hash was checked against the `digest` of the same asset
  on the GitHub release `curl-8_23_0` (they match).
- Redis and SQLite: as pinned in `tests/realrepo/repos.py`.

## How the cases were drawn (`bench/cases/pick.py`)

1. The 10 kinds of change are fixed for every repo (table at the end): 3 easy, 6 hard, 1 "might break".
2. For each kind, candidates come from plain regex scans of the source (`bench/accuracy/cfuncs.py`).
   AeroTrace is never used to find or filter them.
3. Candidates are ordered by `sha256("aerotrace-bench-cases-1/" + repo + "/" + kind + "/" + id)`. The
   first one that passes the kind's filter is taken. A name is used by at most one case per repo.
4. Filters: at most 40 mentions (so the truth can be checked by hand), and each kind must mean what it
   says. "Direct calls only" kinds have 2 to 30 calls and no use as a value. Pointer kinds are used as a
   value at least once (`ptr_remove`: never called by name). `int_to_void` returns `int`. `field_type`
   is a plain integer.
5. Redis and SQLite definitions must be compiled in the default build (`cc -E`, `bench/accuracy/live.py`).
   This was checked on the benchmark machine (Apple M1 Pro, arm64), so arm64-only code can be drawn
   (redis-02 is). The benchmark runs on this machine only.

Reproduce: unpack the three pinned archives into empty folders, then
`python3 -I bench/cases/pick.py <redis> <sqlite> <curl> > bench/cases/cases.json` and
`python3 -I bench/cases/render.py > bench/cases/CASES.md`. A second run gave a byte-identical
`cases.json` (about 2 minutes, most of it `cc -E`).

## Rules fixed now, before the run

- **curl compiled check.** curl's default build needs its `./configure`, run in a temp copy (never a
  user's tree). A curl case whose code is not compiled after configure is replaced by the next candidate
  in seed order.
- **Live use.** For fields, globals and macros, at least one use must be in compiled code. If not, the case is
  replaced the same way. (For example curl-06 and curl-08 may sit behind build options.)
- **Macro variants.** When a macro has several `#define`s under `#if`, the edit is made to all of them.
- **Edit can't be applied.** If the edit is impossible as written (for example the field is a bitfield
  that can't become a pointer), the case is replaced the same way.
- Every replacement is logged in `cases.json` with its reason, and shown in the results.

## Known slant, said up front

- `macro_arity` is a common gap for tools that read the syntax tree after the preprocessor has run, and
  `remove_fn` only fails at link time. How AeroTrace does on them hasn't been checked; they stay in
  either way.
- The filter "at most 40 mentions" favours less-used symbols. Large, heavily used symbols are not tested.

## Cases

| id | kind | level | symbol | where | counts (regex) | why this case |
|---|---|---|---|---|---|---|
| redis-01 | add_param | easy | `src/t_hash.c:hashTemplateLpGetTemplateId` | `src/t_hash.c:1001` | calls 2, values 0, mentions 4 | Direct callers stop compiling; the classic signature change. |
| redis-02 | remove_fn | easy | `src/bitops.c:redisPopCountAarch64` | `src/bitops.c:168` | calls 2, values 0, mentions 3 | Callers fail to compile, or, when a prototype stays in a header, only at link. |
| redis-03 | int_to_void | easy | `src/bitops.c:checkSignedBitfieldOverflow` | `src/bitops.c:616` | calls 2, values 0, mentions 3 | Only callers that use the value break; callers that ignore it still build. |
| redis-04 | ptr_param | hard | `src/socket.c:connSocketGetType` | `src/socket.c:380` | calls 0, values 1, mentions 2 | The break is where the function is put in a table or assigned, not at a call. |
| redis-05 | ptr_remove | hard | `src/sentinel.c:dictInstancesValDestructor` | `src/sentinel.c:397` | calls 0, values 1, mentions 2 | Nothing calls it by name; only the table or assignment that names it breaks. |
| redis-06 | field_rename | hard | `redisServer.aof_rewrite_perc` | `src/server.h` | mentions 3 | Every reader and writer breaks; grep must tell this field from same-named ones. |
| redis-07 | field_type | hard | `redisServer.stat_current_cow_peak` | `src/server.h` | mentions 9 | Arithmetic and integer uses break, some uses still build and may behave differently. Type `size_t`. |
| redis-08 | global_rename | hard | `src/bitops.c:bitsinbyte` | `src/bitops.c:53` | mentions 8 | Every use breaks; globals are often used far from where they are defined. |
| redis-09 | macro_arity | hard | `src/zmalloc.c:MALLOC_MIN_SIZE` | `src/zmalloc.c:56` | mentions 6 | Every use of the macro breaks; macros are a common gap for tools that read the code after the preprocessor. |
| redis-10 | behaviour | might_break | `src/fast_float_strtod.c:read8_to_u64` | `src/fast_float_strtod.c:320` | calls 2, values 0, mentions 3 | Nothing fails to build; callers that use the value may behave differently. |
| sqlite-01 | add_param | easy | `sqlite3.c:sqlite3PagerPagecount` | `sqlite3.c:62938` | calls 4, values 0, mentions 6 | Direct callers stop compiling; the classic signature change. |
| sqlite-02 | remove_fn | easy | `sqlite3.c:jsonBlobAppendOneByte` | `sqlite3.c:214984` | calls 4, values 0, mentions 5 | Callers fail to compile, or, when a prototype stays in a header, only at link. |
| sqlite-03 | int_to_void | easy | `sqlite3.c:pagerIsSuperJrnlName` | `sqlite3.c:60257` | calls 3, values 0, mentions 4 | Only callers that use the value break; callers that ignore it still build. |
| sqlite-04 | ptr_param | hard | `sqlite3.c:sqlite3_db_cacheflush` | `sqlite3.c:189289` | calls 0, values 1, mentions 5 | The break is where the function is put in a table or assigned, not at a call. |
| sqlite-05 | ptr_remove | hard | `sqlite3.c:sqlite3_create_collation16` | `sqlite3.c:192201` | calls 0, values 1, mentions 6 | Nothing calls it by name; only the table or assignment that names it breaks. |
| sqlite-06 | field_rename | hard | `sqlite3.pSavepoint` | `sqlite3.c` | mentions 14 | Every reader and writer breaks; grep must tell this field from same-named ones. |
| sqlite-07 | field_type | hard | `Parse.eOrconf` | `sqlite3.c` | mentions 3 | Arithmetic and integer uses break, some uses still build and may behave differently. Type `u8`. |
| sqlite-08 | global_rename | hard | `sqlite3.c:sqlite3OpcodeProperty` | `sqlite3.c:24500` | mentions 11 | Every use breaks; globals are often used far from where they are defined. |
| sqlite-09 | macro_arity | hard | `sqlite3.c:AtomicLoad` | `sqlite3.c:15115` | mentions 21 | Every use of the macro breaks; macros are a common gap for tools that read the code after the preprocessor. |
| sqlite-10 | behaviour | might_break | `sqlite3.c:sqlite3BtreeCursor` | `sqlite3.c:77526` | calls 4, values 0, mentions 6 | Nothing fails to build; callers that use the value may behave differently. |
| curl-01 | add_param | easy | `lib/select.c:Curl_pollset_check` | `lib/select.c:695` | calls 2, values 0, mentions 4 | Direct callers stop compiling; the classic signature change. |
| curl-02 | remove_fn | easy | `lib/mime.c:mime_is_too_deep` | `lib/mime.c:1126` | calls 2, values 0, mentions 3 | Callers fail to compile, or, when a prototype stays in a header, only at link. |
| curl-03 | int_to_void | easy | `lib/cf-socket.c:socket_close` | `lib/cf-socket.c:535` | calls 5, values 0, mentions 6 | Only callers that use the value break; callers that ignore it still build. |
| curl-04 | ptr_param | hard | `lib/http2.c:on_stream_close` | `lib/http2.c:1340` | calls 0, values 1, mentions 2 | The break is where the function is put in a table or assigned, not at a call. |
| curl-05 | ptr_remove | hard | `lib/http_proxy.c:http_proxy_cf_destroy` | `lib/http_proxy.c:696` | calls 0, values 1, mentions 2 | Nothing calls it by name; only the table or assignment that names it breaks. |
| curl-06 | field_rename | hard | `connectdata.proxy_ntlm_state` | `lib/urldata.h` | mentions 6 | Every reader and writer breaks; grep must tell this field from same-named ones. |
| curl-07 | field_type | hard | `UserDefined.upkeep_interval_ms` | `lib/urldata.h` | mentions 7 | Arithmetic and integer uses break, some uses still build and may behave differently. Type `timediff_t`. |
| curl-08 | global_rename | hard | `lib/socks.c:cf_socks_statename` | `lib/socks.c:72` | mentions 2 | Every use breaks; globals are often used far from where they are defined. |
| curl-09 | macro_arity | hard | `lib/mime.h:Curl_mime_set_subparts` | `lib/mime.h:167` | mentions 4 | Every use of the macro breaks; macros are a common gap for tools that read the code after the preprocessor. |
| curl-10 | behaviour | might_break | `lib/curl_share.c:share_in_use` | `lib/curl_share.c:163` | calls 2, values 0, mentions 3 | Nothing fails to build; callers that use the value may behave differently. |

## Kinds of change

| kind | level | the edit |
|---|---|---|
| add_param | easy | add an int parameter to the definition and every prototype; call sites unchanged |
| remove_fn | easy | delete the definition (prototypes in headers stay) |
| int_to_void | easy | change the return type from int to void (definition, prototypes, return statements) |
| ptr_param | hard | add an int parameter to a function that is also stored as a pointer |
| ptr_remove | hard | delete a function that is reached only through a pointer (no direct calls) |
| field_rename | hard | rename a struct field (add the suffix _renamed) |
| field_type | hard | change an integer field to a pointer type |
| global_rename | hard | rename a file-scope variable |
| macro_arity | hard | add a parameter to a function-like macro |
| behaviour | might_break | change what the function returns in one case; signature unchanged |

## Pool sizes and skips

- redis: 5296 function definitions, 659 fields, 270 file-scope variables, 340 function-like macros. 45 candidates skipped (all in `cases.json`, with reasons).
- sqlite: 4822 function definitions, 228 fields, 30 file-scope variables, 755 function-like macros. 67 candidates skipped (all in `cases.json`, with reasons).
- curl: 2661 function definitions, 196 fields, 180 file-scope variables, 471 function-like macros. 79 candidates skipped (all in `cases.json`, with reasons).
