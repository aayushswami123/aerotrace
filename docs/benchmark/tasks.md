# Benchmark Part B: the 20 tasks

This list was written and committed **before any benchmark run**. Changing a task after the first run
breaks the benchmark: a changed task needs a new id, and the old one stays in the report with its results.
How the tasks are run and judged is in [protocol.md](protocol.md).

Every task was checked against the pinned sources (`tests/realrepo/repos.py`: Redis 8.10.3 tarball,
SQLite 3.54.1 amalgamation). All `file:line` references are to the unpacked, unchanged sources.

## Mix

| Id | Repo | Kind | Main risk | Size |
|---|---|---|---|---|
| R01 | Redis | reorder parameters (same types) | compiler silent | M |
| R02 | Redis | change return value meaning | compiler silent | L |
| R03 | Redis | change parameter type | ~150 call sites, module API must not change | L |
| R04 | Redis | remove parameter from a callback type | callbacks reached only through pointers | M |
| R05 | Redis | remove parameter | caller in a second binary's file | S |
| R06 | Redis | remove parameter | a caller inside `#if USE_OPENSSL` | S |
| R07 | Redis | change return type | 114 uses, 2 binaries | M |
| R08 | Redis | rename struct field (bitfield) | macros, other `lru` names | M |
| R09 | Redis | change struct field type | config table takes its address, compiler only warns | S |
| R10 | Redis | remove struct field | INFO output must stay | S |
| R11 | Redis | move a global into a struct | stray `extern` lines in .c files | S |
| R12 | Redis | remove globals | RDB loading of inf/nan | S |
| R13 | Redis | remove a function | 51 call sites, 14 files | L |
| R14 | Redis | rename a function | generated `commands.def` and its JSON source | S |
| R15 | Redis | move functions to a new file | build file must list it | M |
| S01 | SQLite | rename struct field | 7 other structs have a field of the same name, 2 of them compiled out | L |
| S02 | SQLite | reorder parameters | literal `0` arguments hide mistakes | M |
| C01 | Redis | control: rename a `static` function | low | S |
| C02 | Redis | control: remove a dead struct field | low | S |
| C03 | SQLite | control: rename a `static` function | low | S |

Size: **S** = under 20 changed lines in at most 3 files; **M** = 20 to 150 lines or 4 to 10 files;
**L** = more than 150 lines or more than 10 files.

The three control tasks (C01 to C03) are there to keep us honest: a plain text search finds everything
they touch, so AeroTrace should not change the outcome much. If it does (good or bad), the report says so.

## What every task shares

**Prompt.** The text given to the agent is the task's *Prompt* block followed by the suffix for its repo,
exactly as written here, on both sides (with and without AeroTrace).

Redis suffix:

```text
You are in the root of the Redis 8.10.3 source tree. Build with `make -C src -j8 MALLOC=libc`. Tests run
with `./runtest --single <unit>`, for example `./runtest --single unit/type/string`. When you are done, the
build must succeed with no compiler warnings and the existing tests must still pass. Do not edit the Tcl
tests under tests/. Do not commit.
```

SQLite suffix:

```text
You are in a folder with the SQLite 3.54.1 amalgamation (sqlite3.c, sqlite3.h, shell.c). Build with
`cc -O1 -Wall -o sqlite3 shell.c sqlite3.c`. When you are done, it must build with no compiler warnings
and behave exactly as before. Do not commit.
```

**Judging.** After the agent stops, the judge runs these in order on the agent's final tree (details and
the exact scripts are in [protocol.md](protocol.md#judging-a-run)):

1. **Build.** Redis: `make -C src clean && make -C src -j8 MALLOC=libc`. SQLite:
   `cc -O1 -Wall -o sqlite3 shell.c sqlite3.c`. Passes when the exit code is 0 and the log has no line
   containing `warning:`. (Both unchanged sources build with 0 warnings here; see protocol.md.)
2. **Tests.** Redis: the task's `./runtest` line; passes on exit code 0. SQLite: the smoke script
   (protocol.md, "SQLite smoke test") gives byte-for-byte the same output as the unchanged build.
3. **Checks.** The task's check commands. Each one says what it must print.

A task is **solved** only when all three pass. `argcount.py` is the small script in
[protocol.md](protocol.md#argcountpy); it prints every use of a function with the wrong number of
arguments and exits 1 if there is one. All commands run from the repo root.

---

## R01: reorder `stringmatchlen` parameters

- **Repo:** Redis. **Kind:** signature change, reorder. **Size:** M (1 definition, 1 prototype, 15 calls in 8 files).
- **Source:** definition `src/util.c:239`, prototype `src/util.h:42`. Calls: `src/acl.c:1621`,
  `src/acl.c:1678`, `src/db.c:1639`, `src/db.c:1716`, `src/db.c:2003`, `src/db.c:2045`, `src/db.c:2093`,
  `src/db.c:2137`, `src/functions.c:540`, `src/pubsub.c:509`, `src/pubsub.c:718`, `src/server.c:5889`,
  `src/t_array.c:1155`, `src/util.c:246` (inside `stringmatch`), `src/util.c:260` (fuzz test).

Prompt:

```text
Change the parameter order of stringmatchlen() so the string being tested comes first and the pattern
second. The new signature, in both src/util.c and src/util.h, must be exactly:

int stringmatchlen(const char *string, int stringLen, const char *pattern, int patternLen, int nocase)

Update every caller so that Redis behaves exactly as before. Do not change stringmatchlen_impl() or
stringmatch().
```

**Why it is risky.** The two pairs being swapped have the same types (`const char *`, `int`), so a missed
or half-updated call still compiles with no warning, and matching silently goes the wrong way. Callers sit
in ACL checks, KEYS/SCAN, pub/sub patterns, FUNCTION LIST and COMMAND LIST. `stringmatch()` and the fuzz test
in `util.c` also call it. Comments mention `stringmatchlen()` in `cluster.c:34` and `debug.c:522` (not calls).

**Judging.**
- Tests: `./runtest --clients 8 --timeout 1200 --single unit/keyspace --single unit/scan --single unit/pubsub --single unit/acl --single unit/functions --single unit/introspection-2 --single unit/type/array`
- Check 1, new definition: `tr -s ' \t\n' ' ' < src/util.c | grep -c 'int stringmatchlen(const char \*string, int stringLen, const char \*pattern, int patternLen, int nocase) {'` prints `1`.
- Check 2, new prototype: `tr -s ' \t\n' ' ' < src/util.h | grep -c 'int stringmatchlen(const char \*string, int stringLen, const char \*pattern, int patternLen, int nocase);'` prints `1`.
- Check 3, nothing dropped: `python3 -I argcount.py src stringmatchlen 5` exits 0.

## R02: `string2ll` returns `C_OK` / `C_ERR`

- **Repo:** Redis. **Kind:** return value meaning change. **Size:** L (about 50 call sites in 22 files).
- **Source:** definition `src/util.c:487`, prototype `src/util.h:52`. Callers include `src/networking.c:3305`
  and `:3387` (request parsing), `src/object.c:907`, `src/t_set.c:100`, `src/config.c:2258`,
  `src/resp_parser.c:48` (7 calls), `src/quicklist.c:1341`, `src/util.c:560` and `:579` (`string2l`),
  and the unit tests in `src/util.c:1546` to `:1592`, which only compile with `-DREDIS_TEST`.

Prompt:

```text
Make string2ll() in src/util.c follow the usual Redis convention: return C_OK (0) when the string was
parsed and C_ERR (-1) when it was not, instead of 1 and 0 today. Keep the signature otherwise the same.
Update every caller in the Redis server sources so behaviour stays exactly the same, including the C unit
tests in src/util.c that are built with -DREDIS_TEST. Do not change the separate copy of string2ll in
deps/hiredis.
```

**Why it is risky.** Callers test the result in many shapes (`if (string2ll(...))`, `!string2ll(...)`,
`== 0`, `return string2ll(...)`, assignment to a variable checked later). All of them still compile after the
change, so every missed caller flips silently. Request parsing in `networking.c` depends on it. Some callers
are only compiled with `-DREDIS_TEST`. `deps/hiredis/read.c:162` has its own `static` `string2ll` with a
different contract that must stay as it is.

**Judging.**
- Tests: `./runtest --clients 8 --timeout 1200 --single unit/type/incr --single unit/type/string --single unit/type/list --single unit/type/hash --single unit/type/set --single unit/type/zset --single unit/protocol --single unit/expire --single unit/keyspace --single unit/scan --single unit/scripting`
- Check 1, C unit tests (runs last, it rebuilds): `make -C src clean && make -C src -j8 MALLOC=libc REDIS_CFLAGS=-DREDIS_TEST && ./src/redis-server test util` exits 0 (unchanged sources: exit 0, about 27 s to build).
  The tests in `util.c` assert the return values, so they only pass if they were updated to the new
  contract and the function follows it.
- Check 2, hiredis copy untouched: `cmp deps/hiredis/read.c <pristine>/deps/hiredis/read.c` exits 0.

## R03: `notifyKeyspaceEvent` takes `redisDb *` instead of a db id

- **Repo:** Redis. **Kind:** parameter type change. **Size:** L (about 130 + 22 call sites in 19 files).
- **Source:** `src/notify.c:276` (`notifyKeyspaceEvent`), `src/notify.c:281`
  (`notifyKeyspaceEventWithSubkeys`), prototypes `src/server.h:4144` and `:4145`. Calls that do not pass
  `c->db->id`: `src/cluster.c:1781` (`server.db[0].id`), `src/t_stream.c:3505` (`dbid`),
  `src/db.c:2411` (`src->id`), `src/module.c:9693` (`ctx->client->db->id`), `src/t_zset.c:3363`.

Prompt:

```text
Change notifyKeyspaceEvent() and notifyKeyspaceEventWithSubkeys() (src/notify.c) so that their fourth
parameter is `redisDb *db` instead of `int dbid`. Update the prototypes in src/server.h and every caller to
pass the database pointer. Inside notify.c use db->id where the number is needed. Do not change the module
API (moduleNotifyKeyspaceEvent and the RM_* functions keep their current signatures).
```

**Why it is risky.** Very many callers, spread over 19 files; a few pass a db id that is not `c->db->id`
and need the right `redisDb` pointer. The module notification path takes an `int dbid` and must stay as it
is. The compiler catches an int passed where a pointer is expected, but not a wrong pointer.

**Judging.**
- Tests: `./runtest --clients 8 --timeout 1200 --single unit/pubsub --single unit/keyspace --single unit/expire --single unit/type/hash --single unit/type/hash-field-expire --single unit/moduleapi/keyspace_events --single unit/moduleapi/postnotifications`
- Check 1: `tr -s ' \t\n' ' ' < src/server.h | grep -c 'void notifyKeyspaceEvent(int type, const char \*event, robj \*key, redisDb \*db);'` prints `1`.
- Check 2: `python3 -I argcount.py src notifyKeyspaceEvent 4` exits 0.
- Check 3, module API unchanged: `grep -c 'void moduleNotifyKeyspaceEvent(int type, const char \*event, robj \*key, int dbid, robj \*\*subkeys, int count);' src/server.h` prints `1` (as in the unchanged file, `src/server.h:3256`).

## R04: drop the `id` parameter from `aeTimeProc`

- **Repo:** Redis. **Kind:** remove parameter from a callback type. **Size:** M (1 typedef, 1 call through
  the pointer, 6 callbacks in 6 files, 2 in redis-benchmark).
- **Source:** typedef `src/ae.h:47`; the call through the pointer `src/ae.c:335`. Callbacks:
  `serverCron` `src/server.c:1562`, `activeDefragTimeProc` `src/defrag.c:1968`, `evictionTimeProc`
  `src/evict.c:442`, `IOThreadCron` `src/iothread.c:958`, `moduleTimerHandler` `src/module.c:10273`,
  `showThroughput` `src/redis-benchmark.c:181` (prototype) and `:1651`. They are registered with
  `aeCreateTimeEvent` at `src/server.c:3201`, `src/defrag.c:2127`, `src/evict.c:459`,
  `src/iothread.c:1035`, `src/module.c:10374`, `src/redis-benchmark.c:1004` and `:1728`.

Prompt:

```text
No time event handler uses its `long long id` argument. Remove it from the aeTimeProc type in src/ae.h, so
it becomes:

typedef int aeTimeProc(struct aeEventLoop *eventLoop, void *clientData);

Update the event loop and every time event handler in the source tree (server, benchmark and anything
else that is built by `make -C src`).
```

**Why it is risky.** No handler is called by name: they are passed as function pointers to
`aeCreateTimeEvent`, so a "who calls X" text search finds nothing. One handler lives in `redis-benchmark.c`,
a different binary. A mismatched handler is an error in this clang (incompatible function pointer types),
but only if it is found and compiled.

**Judging.**
- Tests: `./runtest --clients 8 --timeout 1200 --single unit/expire --single unit/other --single unit/info --single unit/maxmemory --single unit/moduleapi/timer --single integration/redis-benchmark`
- Check 1: `tr -s ' \t\n' ' ' < src/ae.h | grep -c 'typedef int aeTimeProc(struct aeEventLoop \*eventLoop, void \*clientData);'` prints `1`.
- Check 2: for each of `serverCron activeDefragTimeProc evictionTimeProc IOThreadCron moduleTimerHandler showThroughput timeProc`: `python3 -I argcount.py src <name> 2` exits 0.

## R05: drop the unused `ver` parameter of `rdbFunctionLoad`

- **Repo:** Redis. **Kind:** remove parameter. **Size:** S.
- **Source:** definition `src/rdb.c:4627` (`UNUSED(ver)` at `:4628`), prototype `src/rdb.h:181`. Calls:
  `src/rdb.c:4915`, `src/functions.c:777`, `src/redis-check-rdb.c:345`.

Prompt:

```text
rdbFunctionLoad() in src/rdb.c never uses its `int ver` parameter. Remove that parameter from the function
and its prototype, and update every caller.
```

**Why it is risky.** Small, but one caller is in `redis-check-rdb.c`, which reads like a separate tool.
Some callers pass `rdbver` from a local; it has to be removed only from this call.

**Judging.**
- Tests: `./runtest --clients 8 --timeout 1200 --single unit/functions --single integration/rdb`
- Check 1: `python3 -I argcount.py src rdbFunctionLoad 4` exits 0.

## R06: drop the unused `ip` parameter of `acceptCommonHandler`

- **Repo:** Redis. **Kind:** remove parameter. **Size:** S.
- **Source:** definition `src/networking.c:1666` (`UNUSED(ip)` at `:1668`), prototype `src/server.h:3338`.
  Calls: `src/socket.c:319`, `src/unix.c:113`, `src/tls.c:874`. All of `tls.c` is inside
  `#if (USE_OPENSSL == 1 ...)` (`src/tls.c:19`), which the default build does not compile.

Prompt:

```text
acceptCommonHandler() in src/networking.c never uses its `char *ip` parameter. Remove it from the function
and its prototype and update every caller, including code that is only compiled with TLS support.
```

**Why it is risky.** The TLS caller is never compiled in the default build, so the build stays green if it
is missed and the TLS build breaks later. Tools that only see compiled code miss it too.

**Judging.**
- Tests: `./runtest --clients 8 --timeout 1200 --single unit/networking --single unit/introspection --single unit/auth`
- Check 1 (includes `tls.c`): `python3 -I argcount.py src acceptCommonHandler 2` exits 0.

## R07: `listAddNodeHead` / `listAddNodeTail` return `void`

- **Repo:** Redis. **Kind:** return type change. **Size:** M (2 definitions, 2 prototypes, 1 caller that
  uses the result, 114 + uses in 31 files that do not).
- **Source:** `src/adlist.c:76`, `src/adlist.c:109`, prototypes `src/adlist.h:57` and `:58`. The only use
  of the result: `src/adlist.c:279` (`listDup`). 30 calls are in `src/redis-cli.c`, 1 in
  `src/redis-benchmark.c`.

Prompt:

```text
zmalloc() never returns NULL (it aborts on out of memory), so listAddNodeHead() and listAddNodeTail() in
src/adlist.c can never fail. Change both to return void instead of list *, and update every place that
uses their return value.
```

**Why it is risky.** 114 uses look scary, but only one uses the result. The agent has to find that one
(`listDup`'s error path) and not churn the other 113. Uses span three binaries (server, cli, benchmark).

**Judging.**
- Tests: `./runtest --clients 8 --timeout 1200 --single unit/type/list --single unit/pubsub --single unit/multi --single unit/acl --single unit/tracking --single integration/redis-cli`
- Check 1: `grep -nE '^(list \*|void )listAddNode(Head|Tail)\(' src/adlist.c src/adlist.h` shows only `void` lines (4 lines).

## R08: rename the `lru` field of `struct redisObject`

- **Repo:** Redis. **Kind:** struct field rename (bitfield). **Size:** M (21 uses on 19 lines in 5 files).
- **Source:** `src/object.h:110` (`unsigned lru:LRU_BITS;`). Uses: `src/db.c:58`, `:66`, `:322`, `:655`;
  `src/evict.c:77`, `:78`, `:80`, `:302`, `:303`; `src/debug.c:734`; `src/object.c:90`, `:113`, `:125`,
  `:127`, `:191`, `:231`, `:321`, `:1712`, `:1729`.

Prompt:

```text
Rename the `lru` field of struct redisObject (src/object.h) to `lru_or_lfu`, because it holds either LRU
or LFU data. Update every use. Do not rename anything else (functions, macros, config names, INFO fields
and other structs keep their names).
```

**Why it is risky.** The name `lru` is everywhere for other things: `LRU_CLOCK()`, `lru_clock`,
`estimateObjectIdleTime`, jemalloc's `lru` fields in `deps/`, config names, `lfu-simulation.c`, test
names. A blind search and replace breaks or renames the wrong ones; a careful one has to look at types.

**Judging.**
- Tests: `./runtest --clients 8 --timeout 1200 --single unit/maxmemory --single unit/introspection-2 --single unit/dump --single unit/moduleapi/misc`
- Leftover uses of `lru` in compiled code are build errors, so the build step covers them. Two comments
  also say `->lru` (`src/evict.c:54`, `src/object.h:93`); updating them is allowed, not required.
- Check 1: `cat src/*.c src/*.h | grep -oE '(->|\.)lru_or_lfu\b' | wc -l` prints at least `21` (21 uses on
  19 lines in the unchanged code).
- Check 2, nothing else renamed: `git diff --stat -- deps utils tests` prints nothing, and
  `grep -c 'unsigned lru_or_lfu:LRU_BITS;' src/object.h` prints `1`.

## R09: `maxmemory_samples` becomes `long long`

- **Repo:** Redis. **Kind:** struct field type change. **Size:** S.
- **Source:** `src/server.h:2515` (`int maxmemory_samples;`). Uses: `src/config.c:3522`
  (`createIntConfig("maxmemory-samples", ...)`), `src/evict.c:136`, `:141`, `:605`, `:610`.

Prompt:

```text
Change the type of the maxmemory_samples field of struct redisServer (src/server.h) from int to
long long. The maxmemory-samples config must keep working exactly as before (same default, same allowed
range 1 to 64, same CONFIG GET/SET behaviour).
```

**Why it is risky.** The config table stores the field's address as an `int *`. Changing only the field
**still builds**, with one warning (checked on this machine:
`config.c:3522:5: warning: incompatible pointer types initializing 'int *' with an expression of type 'long long *'`),
and config writes then fill only half of the field. Tests may still pass by luck on little-endian machines,
which is why a warning fails the build step.

**Judging.**
- Tests: `./runtest --clients 8 --timeout 1200 --single unit/maxmemory --single unit/introspection`
- Check 1: `grep -cE '^\s+long long maxmemory_samples;' src/server.h` prints `1`.
- Check 2, live config (probe script `probe-r09.sh` in protocol.md): `CONFIG SET maxmemory-samples 64` then
  `CONFIG GET` returns `64`; `CONFIG SET ... 65` and `... 0` are refused; default after start is `5`.

## R10: remove the `arch_bits` field

- **Repo:** Redis. **Kind:** struct field removal. **Size:** S.
- **Source:** `src/server.h:2070` (`int arch_bits;`). Uses: `src/server.c:2420` (set),
  `src/server.c:3223` (32-bit warning), `src/server.c:6446` (INFO `arch_bits:`).

Prompt:

```text
Remove the arch_bits field from struct redisServer. It is a constant, so replace it with a macro
REDIS_ARCH_BITS defined in src/server.h as ((sizeof(long) == 8) ? 64 : 32) and use that everywhere the
field was used. INFO output must not change.
```

**Why it is risky.** Low compile risk; the INFO line uses `%i` with the value, and the macro's type is
`int` only through the conditional expression of int literals, so it stays correct. Checks that the agent
does not change INFO.

**Judging.**
- Tests: `./runtest --clients 8 --timeout 1200 --single unit/info --single unit/info-command`
- Check 1: `grep -cE '^\s+int arch_bits;' src/server.h` prints `0` (unchanged file: `1`), and
  `grep -rn 'server\.arch_bits' src` prints nothing.
- Check 2 (probe): `redis-cli INFO server` contains `arch_bits:64`.

## R11: move the global `ProcessingEventsWhileBlocked` into `server`

- **Repo:** Redis. **Kind:** global variable change. **Size:** S (11 uses in 3 files).
- **Source:** definition `src/networking.c:42`. Uses: `src/networking.c:5692`, `:5707`, `:5708`;
  `src/server.c:1957`, `:2095`, `:2167`, `:2197`; `src/iothread.c:578`. Separate `extern int` declarations
  written inside .c files (not in a header): `src/iothread.c:563`, `src/server.c:1931`.

Prompt:

```text
Move the global variable ProcessingEventsWhileBlocked (defined in src/networking.c) into struct
redisServer as `int processing_events_while_blocked`, initialised to 0, and use
server.processing_events_while_blocked everywhere. Remove the old global and every declaration of it.
```

**Why it is risky.** The two `extern int ProcessingEventsWhileBlocked;` lines live in .c files, not in a
header, so a header-based search does not show them, and leftover `extern` lines compile fine on their own.

**Judging.**
- Tests: `./runtest --clients 8 --timeout 1200 --single unit/scripting --single unit/functions --single integration/rdb --single unit/multi`
- Check 1: `grep -rn 'ProcessingEventsWhileBlocked' src` prints nothing.
- Check 2: `grep -c 'processing_events_while_blocked' src/server.h` prints at least `1`.

## R12: remove the `R_Zero`, `R_PosInf`, `R_NegInf`, `R_Nan` globals

- **Repo:** Redis. **Kind:** global removal. **Size:** S.
- **Source:** definition `src/server.c:80`, declaration `src/server.h:3200`, set at `src/server.c:2535`
  to `:2538`, used at `src/rdb.c:651`, `:652`, `:653` (`rdbLoadDoubleValue`).

Prompt:

```text
Remove the global doubles R_Zero, R_PosInf, R_NegInf and R_Nan. Use INFINITY, -INFINITY and NAN from
<math.h> where they were used, and delete their initialisation.
```

**Why it is risky.** Small, but the uses are in RDB loading of old-format doubles, far from where the
globals are defined, and the comment at `src/server.c:77` warns about compiler optimisations.

**Judging.**
- Tests: `./runtest --clients 8 --timeout 1200 --single unit/type/zset --single integration/rdb --single unit/dump`
- Check 1: `grep -rnE '\bR_(Zero|PosInf|NegInf|Nan)\b' src` prints nothing.

## R13: remove `lookupKeyRead`

- **Repo:** Redis. **Kind:** function removal. **Size:** L (51 calls in 14 files).
- **Source:** definition `src/db.c:356` (body: `return lookupKeyReadWithFlags(db,key,LOOKUP_NONE);`),
  prototype `src/server.h:4262`. Calls in `bitops.c` (6), `cluster.c` (2), `db.c` (4), `expire.c` (1),
  `geo.c` (3), `hyperloglog.c` (3), `sort.c` (2), `t_array.c` (9), `t_hash.c` (2), `t_list.c` (1),
  `t_set.c` (4), `t_stream.c` (7), `t_string.c` (4), `t_zset.c` (3).

Prompt:

```text
Remove the function lookupKeyRead() from src/db.c and its prototype. Replace every call with
lookupKeyReadWithFlags(db, key, LOOKUP_NONE), which is exactly what it did.
```

**Why it is risky.** Many mechanical edits; the compiler catches leftovers, so the risk is in time and
tokens, and in near-miss names (`lookupKeyReadOrReply`, `lookupKeyReadWithFlags`) that must stay.

**Judging.**
- Tests: `./runtest --clients 8 --timeout 1200 --single unit/type/string --single unit/type/hash --single unit/type/set --single unit/type/zset --single unit/type/list --single unit/type/stream --single unit/type/array --single unit/bitops --single unit/geo --single unit/hyperloglog --single unit/sort --single unit/expire`
- Check 1: `grep -rnw 'lookupKeyRead' src` prints nothing.
- Check 2: `grep -c 'lookupKeyReadOrReply(client' src/server.h` prints `1` (unchanged).

## R14: rename `lindexCommand`

- **Repo:** Redis. **Kind:** function rename through generated code. **Size:** S.
- **Source:** definition `src/t_list.c:621`, prototype `src/server.h:4573`, command table
  `src/commands.def:12924` (generated), its source `src/commands/lindex.json:8`
  (`"function": "lindexCommand"`), generator `utils/generate-command-code.py`.

Prompt:

```text
Rename the command handler lindexCommand() to listIndexCommand(). Keep the generated command table
consistent with its JSON source.
```

**Why it is risky.** The handler is never called by name; it is referenced from a generated table.
Editing only `commands.def` builds, but the next regeneration from the JSON brings the old name back.

**Judging.**
- Tests: `./runtest --clients 8 --timeout 1200 --single unit/type/list --single unit/introspection-2`
- Check 1: `grep -rnw 'lindexCommand' src` prints nothing.
- Check 2, table matches its source: `cp src/commands.def /tmp/agent.def && python3 utils/generate-command-code.py && cmp src/commands.def /tmp/agent.def`
  exits 0 (unchanged sources regenerate byte-identical; checked).

## R15: move the `get*FromObjectOrReply` functions to a new file

- **Repo:** Redis. **Kind:** refactor across files. **Size:** M.
- **Source:** `src/object.c:1156` (`getDoubleFromObjectOrReply`), `:1190` (`getLongDoubleFromObjectOrReply`),
  `:1241` (`getLongLongFromObjectOrReply`), `:1255` (`getLongFromObjectOrReply`), `:1271`
  (`getRangeLongFromObjectOrReply`), `:1284` (`getPositiveLongFromObjectOrReply`), `:1292`
  (`getIntFromObjectOrReply`); prototypes `src/object.h:170` to `:181`; object list `REDIS_SERVER_OBJ` at
  `src/Makefile:404`.

Prompt:

```text
Move the seven get*FromObjectOrReply() functions (getDoubleFromObjectOrReply, getLongDoubleFromObjectOrReply,
getLongLongFromObjectOrReply, getLongFromObjectOrReply, getRangeLongFromObjectOrReply,
getPositiveLongFromObjectOrReply, getIntFromObjectOrReply) out of src/object.c into a new file
src/object_reply.c. Keep their prototypes in src/object.h. Add the new file to the build. Do not change
what the functions do.
```

**Why it is risky.** The new file must be added to `REDIS_SERVER_OBJ` in `src/Makefile`, and it needs the
right includes. The functions call helpers in `object.c` (`getDoubleFromObject`, `getLongLongFromObject`,
...) that must stay visible.

**Judging.**
- Tests: `./runtest --clients 8 --timeout 1200 --single unit/type/incr --single unit/type/list --single unit/type/zset --single unit/expire --single unit/geo`
- Check 1: `grep -cE '^int get(Double|LongDouble|LongLong|Long|RangeLong|PositiveLong|Int)FromObjectOrReply\(' src/object_reply.c` prints `7`.
- Check 2: the same grep on `src/object.c` prints `0`.
- Check 3: `grep '^REDIS_SERVER_OBJ=' src/Makefile | grep -c 'object_reply.o'` prints `1`.

## S01: rename `Parse.nErr`

- **Repo:** SQLite. **Kind:** struct field rename. **Size:** L (about 150 uses in `sqlite3.c`).
- **Source:** `sqlite3.c:21121` (`int nErr;` in `struct Parse`, which starts at `:21086`). Other structs
  with a field named `nErr` that must keep it: `IntegrityCk` (`sqlite3.c:72392`), `JsonParse` (`:214135`),
  `RtreeCheck` (`:223593`), `GeoParse` (`:224196`), and in `shell.c`: `ShellState` (`:25090`), `ImportCtx`
  (`:30549`). There is also a local variable `nErr` in `OP_IntegrityCk` (`sqlite3.c:103720`).
  `RtreeCheck` and `GeoParse` are inside `SQLITE_ENABLE_RTREE` / `SQLITE_ENABLE_GEOPOLY`, which the default
  build does not compile.

Prompt:

```text
In sqlite3.c, rename the field nErr of struct Parse to nParseErr, and update every use of that field.
Other structs that also have a field called nErr (in sqlite3.c and shell.c) must keep their field names.
```

**Why it is risky.** A name shared by seven structs. Renaming every `->nErr` breaks the build or, worse,
renames fields in code that is compiled out (R-Tree, Geopoly) without any error.

**Judging.**
- Tests: SQLite smoke test (protocol.md).
- Check 1: `grep -cE '^\s+int nParseErr;' sqlite3.c` prints `1`.
- Check 2, other fields kept: `grep -cE '^\s+(int|u8)\s+nErr;' sqlite3.c` prints `5` (6 in the unchanged file)
  and the same grep on `shell.c` prints `2`.
- Check 3, compiled-out code still builds: `cc -O0 -Wall -c -o /tmp/ext.o -DSQLITE_DEBUG -DSQLITE_ENABLE_RTREE -DSQLITE_ENABLE_GEOPOLY -DSQLITE_ENABLE_FTS3 -DSQLITE_ENABLE_FTS5 -DSQLITE_ENABLE_SESSION -DSQLITE_ENABLE_PREUPDATE_HOOK -DSQLITE_ENABLE_STAT4 -DSQLITE_ENABLE_DBSTAT_VTAB sqlite3.c`
  exits 0 with no `warning:` (unchanged file: 0 warnings, 3 s).

## S02: swap the last two parameters of `sqlite3ExprAlloc`

- **Repo:** SQLite. **Kind:** signature change, reorder. **Size:** M (26 calls).
- **Source:** prototype `sqlite3.c:22204`, definition `sqlite3.c:113816`. Calls include
  `sqlite3.c:110710`, `:113048` (`pCollName, dequote`), `:113860`, `:114063`, `:129258`, `:139113` to
  `:139158` (foreign key actions), `:185318` and `:185355` (generated parser actions).

Prompt:

```text
In sqlite3.c, change sqlite3ExprAlloc() so that its `dequote` flag comes before the token. The new
signature (prototype and definition) must be:

Expr *sqlite3ExprAlloc(sqlite3 *db, int op, int dequote, const Token *pToken)

(keep SQLITE_PRIVATE as it is). Update every caller so behaviour stays the same.
```

**Why it is risky.** Most mistakes are int/pointer mix-ups the compiler rejects, but 11 calls pass
`0, 0`, which compiles either way, so the agent can't use the compiler as the only guide for which calls it
has already done. Two callers are inside the generated parser.

**Judging.**
- Tests: SQLite smoke test.
- Check 1: `tr -s ' \t\n' ' ' < sqlite3.c | grep -c 'Expr \*sqlite3ExprAlloc(sqlite3\*, *int, *int, *const Token\*);'`
  prints `1` (prototype; the original prototype has no parameter names), and the definition is checked by
  `grep -A4 '^SQLITE_PRIVATE Expr \*sqlite3ExprAlloc($' sqlite3.c | grep -oE '(sqlite3 \*db|int op|int dequote|const Token \*pToken)' | tr '\n' ,`
  printing `sqlite3 *db,int op,int dequote,const Token *pToken,` (the unchanged file prints
  `sqlite3 *db,int op,const Token *pToken,int dequote,`).
- Check 2: `python3 -I argcount.py . sqlite3ExprAlloc 4 sqlite3.c` exits 0.

## C01 (control): rename `checkStringLength`

- **Repo:** Redis. **Kind:** rename a `static` function. **Size:** S.
- **Source:** `src/t_string.c:26` (`static int checkStringLength(...)`); calls `src/t_string.c:604`, `:623`,
  `:1391`.

Prompt:

```text
Rename the static function checkStringLength() in src/t_string.c to stringLengthAllowedOrReply().
```

**Why it is low risk.** One file, three calls, `static`: a text search finds everything.

**Judging.**
- Tests: `./runtest --clients 8 --timeout 1200 --single unit/type/string`
- Check 1: `grep -rnw 'checkStringLength' src` prints nothing; `grep -c 'stringLengthAllowedOrReply(' src/t_string.c` prints `4`.

## C02 (control): remove the dead field `clients_pending_read`

- **Repo:** Redis. **Kind:** remove a field that nothing reads. **Size:** S.
- **Source:** `src/server.h:2109` (`list *clients_pending_read;`); its only use `src/server.c:3035`
  (`server.clients_pending_read = listCreate();`).

Prompt:

```text
The clients_pending_read field of struct redisServer is created at startup but never used. Remove the field
and its initialisation.
```

**Why it is low risk.** One write, no reads, two files.

**Judging.**
- Tests: `./runtest --clients 8 --timeout 1200 --single unit/networking --single unit/info`
- Check 1: `grep -rn 'clients_pending_read' src` prints nothing.

## C03 (control): rename `resolve_backslashes`

- **Repo:** SQLite. **Kind:** rename a `static` function. **Size:** S.
- **Source:** `shell.c:30345` (`static void resolve_backslashes(char *z)`); calls `shell.c:34917`,
  `shell.c:38351`.

Prompt:

```text
In shell.c, rename the static function resolve_backslashes() to shellResolveBackslashes().
```

**Why it is low risk.** One file, two calls, `static`.

**Judging.**
- Tests: SQLite smoke test.
- Check 1: `grep -c 'resolve_backslashes' shell.c` prints `0`; `grep -c 'shellResolveBackslashes(' shell.c` prints `3`.
