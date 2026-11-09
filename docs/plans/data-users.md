# Plan: data users of struct fields and globals (item 5, Part B)

Work item 4. Branch `feat/data-users`, based on `launch-candidate`. Part A (`signature.py`, `certainty.py`)
is merged; this part should look and feel the same. Part C (later) calls this from `diff` / `check`.

## Goal

Before an agent renames, removes or retypes a struct field, or changes a global, it asks:

```
aerotrace data redisServer::hz          # CLI
data_users(name="redisServer::hz")      # MCP
```

and gets, ranked and capped:

- every function that reads or writes it, with the lines;
- every global initializer that sets it (`{ .field = value }` at file scope, e.g. command tables);
- every place that still uses it after it was removed (compile error "no member named");
- every place its address leaves code we can follow (`foo(&x)`, `p = &x`, `return &x`), as **unknown**;
- for a global: the callers of those functions, to a small depth;
- exact / probable / unknown on every result, with a reason, and the limits of the answer.

## What the v3 index already records (measured on the pinned repos)

Built in my own temp dir (Redis 8.10.3: 5064 functions, 5063 variables; SQLite 3.54.1: 3244 / 3347).

| Question | Table | Redis | SQLite | Enough? |
|---|---|---|---|---|
| who reads / writes a field or global, which line | `edges` kind reads/writes (one row per line) | 38900 reads, 7663 writes | yes | yes |
| designated initializer at file scope | `initializers` | 5507 rows (`static_configs`, `redisCommandTable` `.args=` / `.subcommands=`) | **0** | only designated, see D1 |
| address leaves (`foo(&x)`) | `escapes` | 1563 | 851 | inside functions only, see D2; false rows, see #18 |
| field used after removal | `missing_members` | 0 (clean tree) | 0 | yes (fixture shows it) |
| two structs, same field name | `variables.container` | `redisServer::dirty` vs `aofManifest::dirty` | `pNext` in 37 structs | yes |
| same struct defined twice | `variables.clash` | - | `sqlite3_vfs::pNext` in `sqlite3.c:1838` and `sqlite3.h:1517` | yes |
| static global, same name, same file name | `variables` id `<usr>@<file>` | - | - | yes (fixture `a/state.c`, `b/state.c`) |
| static or extern global | not stored; the USR shows it (`c:lazyfree.c@lazyfree_objects` vs `c:@server`) | | | yes, derived |

Checked by hand: `redisServer::hz` has 50 access rows in 21 functions, and none of them is a
`RedisModuleCronLoopInfo::hz` access. `sqlite3::busyTimeout` has exactly the 4 compiled uses
(`sqlite3.c:190136` is in an `#else` branch that is not compiled, and is correctly absent).
`aofManifest::dirty` has 13 rows, all in `src/aof.c`; `aof.c:711` (`server.aof_manifest->dirty = 1;`) is not
counted as a write of `redisServer::dirty`.

What the index can **not** answer today (owner decisions D1, D2 below):

1. **Positional initializers.** `redisCommandTable` (`src/commands.def:12798`) sets `redisCommand::proc` by
   position through `MAKE_CMD(...)`; SQLite's `sqlite3Config = { ... }` (`sqlite3.c:24362`) sets every
   field by position. Removing or reordering such a field breaks these tables, and the index has no row.
   Same inside functions: `RedisModuleCronLoopV1 ei = {REDISMODULE_CRON_LOOP_VERSION,server.hz};`
   (`src/server.c:1850`) sets `RedisModuleCronLoopInfo::hz`.
2. **Addresses taken inside a file-scope initializer.** `createIntConfig("hz", ..., server.config_hz, ...)`
   (`src/config.c:3537`) expands to `.config.i = &(config_addr)` (`src/config.c:2368`): the address of
   `server.config_hz` is stored in the `static_configs` table, and `CONFIG SET` writes through it. The index
   records nothing for it: no read, no escape. An answer for `redisServer::config_hz` would miss its main
   writer and not say so. This is the "wrong safe answer" case.

## API (for the CLI, MCP and Part C)

New module `src/aerotrace/analysis/data.py`. No analysis in `cli.py` / `mcp/`.

```python
@dataclass(frozen=True)
class Var:                      # a row of `variables`, or a field that is gone (exists=False)
    id: str; name: str; qualified_name: str; container: str | None
    kind: str                   # field | global | static_local
    type: str; file: str; line: int; clash: bool
    exists: bool = True
    @property
    def is_static(self) -> bool  # from the USR: file-scope static (c:<file>@name)

@dataclass
class Use:                      # one function that reads and/or writes it
    fn: Fn
    reads: list[int]; writes: list[int]     # lines
    certainty: str; reason: str

@dataclass
class InitSite:                 # a global's initializer sets this field
    variable: Var; file: str; line: int; certainty: str; reason: str

@dataclass
class Broken:                   # "no member named X in S": still uses a field that is gone
    file: str; line: int; fn: Fn | None; record: str; certainty: str; reason: str

@dataclass
class Escape:                   # its address goes where we can't follow it
    fn: Fn; line: int; detail: str; certainty: str = UNKNOWN; reason: str = ""

@dataclass
class DataUsers:
    target: Var
    question: str               # "declaration" (rename/remove/retype) | "value" (see `writer`)
    writer: Fn | None           # value question: what reads what this function writes
    uses: list[Use]             # ranked
    initializers: list[InitSite]
    broken: list[Broken]
    escapes: list[Escape]
    upstream: list[Hit]         # callers of the users, distance 1..depth (globals; fields with --depth)
    depth: int; truncated: bool
    same_name: list[Var]        # other fields/globals with this name, NOT included (so the agent knows)
    other_copies: list[Var]     # same USR defined in another file (sqlite3_vfs in sqlite3.c and sqlite3.h)
    blind_spots: list[str]

def find_variables(conn, query: str) -> list[Var]
    # "Struct::field", "global", "name@file", "Struct::field@file", "global.field" (a global's struct type).
    # Several matches -> all returned, the caller lists them and asks (never picks silently), like symbols.find.
def gone_field(conn, record: str, member: str) -> Var | None
    # a field with no `variables` row but with `missing_members` rows (it was removed): Var(exists=False)
def data_users(conn, var_id: str, depth: int | None = None, writer_id: str | None = None) -> DataUsers
def data_users_of(conn, var: Var, depth: int | None = None, writer_id: str | None = None) -> DataUsers
    # same, for a Var you already have (gone fields have no id)
```

Part C calls `data_users(conn, changed_var.id)` for every changed declaration, and
`data_users(conn, var_id, writer_id=fn.id)` for every variable a changed function writes.

Depth: `None` means the default: **2 for globals** (callers of the users and their callers, like
`signature`), **0 for fields** (the work item asks for users only; callers of 100 functions that write
`server.dirty` are noise). `--depth N` overrides both.

## CLI and MCP

```
aerotrace data <Struct::field | global | name@file | global.field> [--depth N] [--from FUNCTION]
               [--limit N | --all] [--json] [--root DIR]
```

MCP: `data_users(name, depth=None, from_function="", limit=None, show_all=False)`. Tool description:
"Before renaming, removing or retyping a struct field or a global, call this. It lists every function that
reads or writes it, initializers that set it, places that still use it after removal, and where its
address goes. Fields are tracked per struct type, not per object." `INSTRUCTIONS` gets one line.

Several matches: same as functions: list them (`redisServer::dirty@src/server.h`,
`aofManifest::dirty@src/server.h`) and ask again. A bare name that is only a field in several structs
(`dirty`) is several matches.

## Certainty rules

| Result | Level | Reason (as shown) |
|---|---|---|
| Declaration question: a read/write edge, confidence `exact` | exact | "names this field" / "names this global" |
| a read/write edge, confidence `ambiguous` (clashing global the file can't tell apart) | probable | "another global has the same name; this may be the other one" |
| initializer row | exact | "sets it in the initializer of X" |
| `missing_members` row, record = the field's struct | exact | "does not compile now: no member named X in S" |
| `missing_members` row, same member, other record name | probable | "same field name in S2 (may be a typedef of this struct)" |
| escape row | unknown | "its address is passed to foo(); what foo does with it is not seen" (or stored in p / returned) |
| Value question (`--from f`), field | probable | "reads the same field, maybe of another object" (fields are tracked per type, not per object) |
| Value question, global or static local | exact | "reads the same global" |
| Callers further up | from the call link (`certainty.of_call`), weakest on the path | as in `signature` |

For a field DECLARATION change every access is exact: the code names the field, so it must change.
"f writes a->count and g reads b->count" is only probable. This rule goes into `certainty.py` as
`of_access(confidence, var_kind, question) -> (level, reason)`; `MEANING[PROBABLE]` already says "other
object".

## Ranking and caps (aligned with `docs/plans/ranking.md` on `feat/ranking`)

Summary first, then lists, each capped, each ending with "... and N more <things> (use --all to see every
one, or --limit N)" (MCP: `show_all=true`).

Order of the uses list: (1) places that do not compile (`broken`), (2) functions that write it, (3)
functions that only read it; inside each: exact, probable, unknown; then file, first line, function id.
The rank is computed in `data.py` (stored ranked), so CLI, MCP and JSON agree.

| List | Default cap |
|---|---|
| Places that do not compile | 15 |
| Functions that use it (writers first) | 15 |
| Lines per function | 5, then "(+N more lines)" |
| Initializers that set it | 10 (one line per global, with its count of lines) |
| Places its address goes | 10 |
| Callers further up | 15 |
| Most-used files | 5 (one line) |
| Other fields with the same name | 5 (one line) |

JSON: same order; `--limit` / `--all` as in the ranking plan, with neither keep 50 per list; `*_total` and
`*_omitted` keys, a `summary` object, every item has `certainty` and `reason`.

## Output, word for word

These are what the build must print on the pinned repos (counts measured from the v3 index on 2026-11-08;
if the build finds different numbers, the plan is wrong and gets fixed, not the facts).

### A Redis field: `aerotrace data redisServer::hz`

```
Data users of redisServer::hz (src/server.h:2053): field of struct redisServer, type int.
Summary: 21 functions use it in 10 files: 6 write it, 15 only read it (all exact). 0 initializers set it,
0 places pass its address on, 0 places still use it after a removal.
Most-used files: src/server.c (7), src/module.c (3), src/expire.c (2), src/sentinel.c (2), src/t_hash.c (2).
Functions that use it (21), writers first, first 15:
  - updateHZ  src/config.c:2687  writes 2687
  - sentinelFlushConfig  src/sentinel.c:2291  writes 2294, 2296; reads 2291
  - sentinelTimer  src/sentinel.c:5527  writes 5527
  - serverCron  src/server.c:1572  writes 1572, 1579, 1581; reads 1576, 1580 (+15 more lines)
  - initServerConfig  src/server.c:2412  writes 2412
  - initServer  src/server.c:3016  writes 3016
  - asmActiveTrimCycle  src/cluster_asm.c:3765  reads 3765
  - applyWatchdogPeriod  src/debug.c:2808  reads 2808
  - LRU_CLOCK  src/evict.c:65  reads 65
  - activeSubexpiresCycle  src/expire.c:263  reads 263
  - activeExpireCycle  src/expire.c:347  reads 347
  - createLatencyReport  src/latency.c:419  reads 419
  - moduleCreateContext  src/module.c:963  reads 963
  - RM_Yield  src/module.c:2543  reads 2543
  - processModuleLoadingProgressEvent  src/module.c:13218  reads 13218, 13226
  ... and 6 more functions (use --all to see every one, or --limit N).
Not included: 2 other fields named hz (RedisModuleCronLoopInfo::hz, RedisModuleLoadingProgressInfo::hz).
Limits of this answer:
  - Fields are tracked per struct type, not per object: every struct redisServer's hz is listed.
  - Code that reaches it through a void * or a cast to another type, by offset (offsetof, memcpy of the
    whole struct), or from files not analysed, is not seen.
  - Initializers that set fields by position ({1, 2}) are not seen; only {.hz = ...} is.
  - Inside a macro, the line is where the macro is used.
  - (index limits, as in every answer: flags, parse errors, one #ifdef configuration, writes through pointers)
```

(The function line shows the first line it uses the field, not where the function starts: that is the
line the agent must edit. Ranked writers first, then file and line.)

### A Redis global: `aerotrace data lazyfree_objects`

```
Data users of lazyfree_objects (src/lazyfree.c:9): static global (this file only), type _Atomic(size_t).
Summary: 15 functions use it, all in src/lazyfree.c: 14 write it, 1 only reads it (all exact).
0 places pass its address on. 65 callers further up (2 steps): 54 exact, 11 probable.
Functions that use it (15), writers first, first 15:
  - lazyfreeFreeObject  src/lazyfree.c:17  writes 17
  - kvsLazyfreeFree  src/lazyfree.c:70  writes 70
  - lazyFreeTrackingTable  src/lazyfree.c:87  writes 87
  - lazyFreeErrors  src/lazyfree.c:96  writes 96
  - lazyFreeLuaScripts  src/lazyfree.c:107  writes 107
  - lazyFreeFunctionsCtx  src/lazyfree.c:119  writes 119
  - lazyFreeReplicationBacklogRefMem  src/lazyfree.c:131  writes 131
  - freeObjAsync  src/lazyfree.c:236  writes 236
  - emptyDbDataAsync  src/lazyfree.c:348  writes 348
  - freeTrackingRadixTreeAsync  src/lazyfree.c:357  writes 357
  - freeErrorsRadixTreeAsync  src/lazyfree.c:369  writes 369
  - freeLuaScriptsAsync  src/lazyfree.c:380  writes 380
  - freeFunctionsAsync  src/lazyfree.c:390  writes 390
  - freeReplicationBacklogRefMemAsync  src/lazyfree.c:403  writes 403
  - lazyfreeGetPendingObjectsCount  src/lazyfree.c:138  reads 138
Callers further up (65, 2 steps), nearest first, first 15:
  Distance 1 (13):
    - asmTriggerBackgroundTrim  src/cluster_asm.c:3079 via emptyDbDataAsync
    - dbSetValue  src/db.c:576 via freeObjAsync
    - dbGenericDelete  src/db.c:855 via freeObjAsync
    ... (12 exact first, then bioProcessBackgroundJobs  src/bio.c:261 [via_pointer] via kvsLazyfreeFree)
  ... and 50 more callers (use --all to see every one, or --limit N).
[probable] = the link is real, but may not apply here (pointer, virtual call, same name, other object)
Limits of this answer:
  - Stopped at depth 2; more callers exist further up.
  - (field-free limits: void * / casts, files not analysed, macro lines, index limits)
```

The `Callers further up` block reuses the ranking work's grouping once `feat/ranking` lands (grouped by
the pointer call that brings them in); until then it uses today's `_levels` layout.

## Blind spots (stated in every answer)

Always, for fields and globals:

1. Data reached through a `void *` or a cast to another type, or by offset (`offsetof`, `memcpy` of the
   whole struct), is not seen. (A cast back to the real type, `((sqlite3 *)ptr)->busyTimeout`, is seen.)
2. Uses inside macros: the line is where the macro is used; macros or code under other `#ifdef`
   settings are not seen (the index's "one build configuration" note).
3. Initializers that set fields by position are not seen (until D1).
4. Writes made in another function through a pointer (`foo(&x)`) are not seen: that is what the escape
   list is for (already in `graph.index_limits`).

Only when they apply:

- Fields: "Fields are tracked per struct type, not per object."
- Value question on a field: "These may read another object of the same type."
- Extern global: "Code outside the repo that links to it is not seen." (not for static globals)
- Other copies of the same struct (`other_copies`): "struct sqlite3_vfs is also defined in sqlite3.h:1517
  (2 uses there). If both copies must stay the same, ask for sqlite3_vfs::pNext@sqlite3.h too."
- Gone field: "This field is not declared any more. Only places that failed to compile are listed; clang
  stops reporting after its error limit (N errors in a file)." (checked against `files.error_count`)
- Escapes found: "N places pass its address on; reads and writes made through that pointer are not
  listed."
- Truncation, index limits, flag limits (from `graph`).

## Fixture cases

`tests/fixtures/c_data` keeps its files unchanged (Part A tests read its lines). New files
`include/fields.h`, `src/fields.c`, `src/fields_ifdef.c`, written to trip the analysis up:

| Case | Code | Expected |
|---|---|---|
| struct by pointer | `void touch(struct conn *c) { c->count++; }` (exists) | conn::count: touch writes 29, exact |
| array element | `conns[1].count = 0;` (exists), new `tab[i].fd = 0` in a loop over `struct conn *tab` | conn::fd write at that line; conns read |
| same field name, two structs | `conn::count` vs `buf::count` (exists) | buf_count only under buf::count; touch never there; `count` alone -> 4 candidates |
| static globals, same name, same file name | `a/state.c`, `b/state.c` `count` (exists) | `count@a/state.c` -> a_next only; `count@b/state.c` -> b_peek only |
| designated initializers | `defaults = { .fd = -1 }` (exists); new `static struct conn table[] = {{.fd = 1, .count = 2}, {.count = 3}};`; in a function `*c = (struct conn){ .count = 0 };` | 2 initializer lines for conn::count in table; the compound literal is a write |
| positional initializer | `static struct conn pos = { 5, 6 };` | not listed today (blind spot asserted); listed as exact if D1 is approved |
| &field passed on | `fill(&c->count);`, `int *p = &c->fd;`, `return &c->count;` | read + unknown escape "passed to fill()", "address stored in p", "address returned" |
| array element by value | `close_fd(fds[0]);` with `int fds[2]` a global | a read, NOT an escape (#18; known_bug until fixed) |
| removed field still used | `c->gone` in `src/broken.c` (exists) | `conn::gone` -> 1 place, does not compile, in still_uses_gone |
| written through a macro | `#define RESET(c) ((c)->count = 0)` used in `reset_all` | write at the macro use line |
| void * | `void wipe(void *p) { memset(p, 0, sizeof(struct conn)); }` | NOT listed; blind-spot sentence present |
| cast back | `((struct conn *)p)->count++` | listed, exact |
| offsetof | `offsetof(struct conn, count)` | the test records what libclang gives; if not seen, the blind spot must say so |
| other #ifdef | `#ifdef NEVER_DEFINED c->count = 9; #endif` | NOT listed |
| typedef'd anonymous struct | `typedef struct { int count; } anon_t;` | `anon_t::count` resolves; not mixed with conn::count |
| data coupling | `put(struct conn *a)` writes a->count, `get(struct conn *b)` reads b->count; `--from put` | get listed, probable, "maybe another object"; for global g_total `--from log_num`, readers exact |
| global callers | g_total users and `uses()` calling `log_num` | `uses` at distance 1; depth cap reported |
| `global.field` | `struct conn g_main;` `data g_main.fd` | resolves to conn::fd with the per-type note |

Each case is a test in a new `tests/test_data.py`, plus CLI and MCP smoke tests (`test_cli.py`,
`test_mcp.py`) and a golden text output for `aerotrace data conn::count` on the fixture.

## Golden facts to add (hand-checked; proof lines copied from the pinned source)

New fact kind `data` in `tests/realrepo/facts.py`: `query` (a variable), `uses: tuple[Use, ...]`
(function, file, `reads`/`writes`, optional line), `inits: tuple[Line-like]`, `escapes`, `must_not` (function
names that must not be users), `exact` (the list is complete), optional `applied_edits` (edit a copy and
re-index, for the removal case). Function names around each proof line are checked by reading the
source during the build, never from the tool.

Redis 8.10.3:

1. **redisServer::hz writers** (exact list of writers): `src/config.c:2687 server.hz = server.config_hz;`
   (updateHZ), `src/server.c:1572 server.hz = server.config_hz;` and `1579 server.hz *= 2;` (serverCron),
   `src/server.c:2412 server.hz = CONFIG_DEFAULT_HZ;` (initServerConfig), `src/server.c:3016` (initServer),
   `src/sentinel.c:2294`, `2296` (sentinelFlushConfig), `src/sentinel.c:5527` (sentinelTimer). Read through
   a macro: `src/server.h:836 #define run_with_period(_ms_) _run_with_period(server.cronloops, (_ms_), server.hz)`
   used at `src/server.c:1592 run_with_period(100) {` (serverCron reads, line 1592).
   **Must not**: no use of `RedisModuleCronLoopInfo::hz` (`src/redismodule.h:853`) is listed; it is not a
   writer anywhere in serverCron (`src/server.c:1850` only reads server.hz).
2. **Shared field name `dirty`**: `aofManifest::dirty` (`src/server.h:2012 int dirty;`) users are all in
   `src/aof.c`, e.g. `src/aof.c:535 am->dirty = 1;` and `src/aof.c:711 server.aof_manifest->dirty = 1;`.
   **Must not**: `call@src/server.c` (`src/server.c:4021 dirty = server.dirty;`) and
   `initServer@src/server.c` (`src/server.c:3151 server.dirty = 0;`) under aofManifest::dirty; and
   `aofDelHistoryFiles@src/aof.c` is not a writer of `redisServer::dirty` (line 711 writes the manifest's).
   A bare `dirty` gives 2 candidates.
3. **File-scope designated-initializer table**: `redisCommand::subcommands` is set by `redisCommandTable`
   (`src/commands.def:12798 struct COMMAND_STRUCT redisCommandTable[] = {`), e.g. at
   `src/commands.def:12833 ... .subcommands=CLIENT_Subcommands},`; readers `src/server.c:3502 if
   (c->subcommands) {` (populateCommandStructure) and `src/latency.c:487 if (cmd->subcommands) {`.
   **Must not**: the `subcommands_dict` users in `src/acl.c` (another field) are not listed.
4. **Address passed on**: `redisServer::child_info_pipe`: `src/childinfo.c:26 if
   (anetPipe(server.child_info_pipe, O_NONBLOCK, 0) == -1) {` is an unknown escape "passed to anetPipe()".
   **Must not** (known_bug #18 until fixed): `src/childinfo.c:40 close(server.child_info_pipe[0]);` as an
   escape.
5. **Global with callers**: `lazyfree_objects` (`src/lazyfree.c:9 static redisAtomic size_t
   lazyfree_objects = 0;`): writers through macros, e.g. `src/lazyfree.c:17 atomicDecr(lazyfree_objects,1);`
   (all 14 listed), one reader `src/lazyfree.c:138 atomicGet(lazyfree_objects,aux);`; caller at distance 1:
   genRedisInfoString (`src/server.c:6597 "lazyfree_pending_objects:%zu\r\n", lazyfreeGetPendingObjectsCount(),`).
   **Must not**: lazyfreeGetFreedObjectsCount (`src/lazyfree.c:145 atomicGet(lazyfreed_objects,aux);`) and
   lazyfreeResetStats (`src/lazyfree.c:150`) are not users (they use `lazyfreed_objects`).
6. **Address in a file-scope initializer** (only if D2 is approved; otherwise a known_bug with an issue):
   `redisServer::config_hz` has an unknown escape at `src/config.c:3537 createIntConfig("hz", ...,
   server.config_hz, ...)` (via `src/config.c:2368 .config.i = &(config_addr) \`).

SQLite 3.54.1 (`sqlite3.c`):

7. **sqlite3.busyTimeout** (`sqlite3.c:18979 int busyTimeout;`): readers sqlite3Pragma
   (`sqlite3.c:147978 returnSingleInt(v, db->busyTimeout);`) and sqliteDefaultBusyCallback
   (`sqlite3.c:190115 int tmout = db->busyTimeout;`); writers sqlite3_busy_handler
   (`sqlite3.c:190182 db->busyTimeout = 0;`) and sqlite3_busy_timeout (`sqlite3.c:190235 db->busyTimeout =
   ms;`). Complete list. **Must not**: line 190136 (`int tmout = ((sqlite3 *)ptr)->busyTimeout;`, in the
   `#else` of `#if SQLITE_OS_WIN || !defined(HAVE_NANOSLEEP) || HAVE_NANOSLEEP`, not compiled); sqlite3Pragma
   is not a writer (it calls sqlite3_busy_timeout at 147976, it does not assign).
   Value question `--from sqlite3_busy_timeout`: sqliteDefaultBusyCallback is listed **probable**.
8. **Removed field** (`applied_edits`: delete `int busyTimeout;` at 18979 in a copy, re-index): exactly the
   4 lines above do not compile ("no member named 'busyTimeout'"), each in its function; 190136 is not one.
9. **Field through a macro + other #ifdef**: `Sqlite3Config::bSmallMalloc` (`sqlite3.c:21502`): reader
   sqlite3VdbeSorterInit (`sqlite3.c:107562 if( sqlite3GlobalConfig.bSmallMalloc==0 ){`), writer
   sqlite3_config (`sqlite3.c:188889 sqlite3GlobalConfig.bSmallMalloc = va_arg(ap, int)!=0;`), both through
   `sqlite3.c:16505 #define sqlite3GlobalConfig sqlite3Config`. **Must not**: vdbeMemRenderNum
   (`sqlite3.c:85304`, inside `#if GCC_VERSION>=7000000 && GCC_VERSION<15000000 && defined(__i386__)`).
   Positional `sqlite3.c:24368 0, /* bSmallMalloc */` in `sqlite3Config = {` (24362): listed only with D1.
10. **Same struct defined twice + shared field name**: `sqlite3_vfs::pNext` gives 2 candidates
    (`sqlite3.c:1838`, `sqlite3.h:1517`). `sqlite3_vfs::pNext@sqlite3.c`: sqlite3_vfs_find
    (`sqlite3.c:28040 for(pVfs = vfsList; pVfs; pVfs=pVfs->pNext){`), vfsUnlink, sqlite3_vfs_register
    (`sqlite3.c:28087 pVfs->pNext = vfsList;`). **Must not**: do_meta_command (shell.c:38134, uses the
    sqlite3.h copy; named in `other_copies`), and nothing from `Savepoint::pNext` (`sqlite3.c:189562`,
    sqlite3CloseSavepoints).
11. **SQLite global with callers**: `vfsList` (`sqlite3.c:28020 static sqlite3_vfs * SQLITE_WSD vfsList =
    0;`, used through `#define vfsList GLOBAL(sqlite3_vfs *, vfsList)` at 28021): users exactly
    sqlite3_vfs_find, vfsUnlink, sqlite3_vfs_register; sqlite3_vfs_unregister is a caller at distance 1
    (`sqlite3.c:28109 vfsUnlink(pVfs);`). **Must not**: sqlite3_vfs_unregister as a user; line 28093
    `assert(vfsList);` (NDEBUG is defined at 15354, so the assert is not compiled).

## Files to change

- `src/aerotrace/analysis/data.py` (new): `Var`, `find_variables`, `gone_field`, `data_users`, ranking, limits.
- `src/aerotrace/analysis/certainty.py`: `of_access`.
- `src/aerotrace/analysis/render.py`: `data_text`, `data_json` (shared cap / "and N more" helper with the
  ranking work; whichever lands second reuses the other's).
- `src/aerotrace/cli.py`: `data` command. `src/aerotrace/mcp/server.py`: `data_users` tool, one line in
  `INSTRUCTIONS`.
- Only with D1/D2: `src/aerotrace/frontends/cpp/parse.py` (`scan_initializer`, `visit_expr`),
  `src/aerotrace/index/build.py` (`PARSER_VERSION` 7 -> 8), `tests/golden/*.txt` (new initializer and escape
  lines only), a short addendum to ADR 0007 (same tables, more rows; format stays 3).
- `tests/fixtures/c_data/` (new files), `tests/test_data.py`, `tests/test_cli.py`, `tests/test_mcp.py`,
  `tests/golden/data_c_data.txt`.
- `tests/realrepo/facts.py`, `test_golden.py` (`data` kind, `applied_edits`), `facts_redis.py`,
  `facts_sqlite.py`.
- `README.md` command list.

## Risks

- **A wrong safe answer from what the index never saw** (positional initializers, `&x` in a file-scope
  table, `void *`). Mitigation: D1/D2, and limit sentences that are always printed, never hidden.
- **Macro lines**: a use inside a long macro call is reported at the macro's first line
  (`genRedisInfoString` reads server.hz at `src/server.c:6458`, inside `FMTARGS(` at 6437; the index says
  6437). Facts check the function, and the line only where there is no macro.
- **clang's error limit** may cut `missing_members` in a file with many errors. The fixture tests 25 uses of
  a gone field in one file; if clang stops, the answer says so (from `files.error_count`).
- **Typedef names in errors**: "no member named x in 'client'" names the typedef; when it differs from the
  struct tag we can't link them (no typedef table). Shown as probable, same member name.
- **Escape false positives** (#18) add noise to the unknown list until fixed.
- **Anonymous union names** contain a machine path (#19); `numericConfigData::...::i` can't be typed.
  Not worked around here.
- **Big answers**: `server` (Redis global) has 1167 users, `redisServer::dirty` 102. Caps + summary keep
  them small; JSON keeps everything with `--all`.
- **Real-repo time**: fact 8 re-indexes SQLite once (~5 s).

## Decisions for the owner

- **D1. Record positional initializers** (`{5, 6}`, `MAKE_CMD(...)`, `sqlite3Config = {...}`) as
  `initializers` rows (file scope) and as writes (inside functions). Same table, same format 3; parser
  change, `PARSER_VERSION` 8, old caches re-parse. Without it, field answers miss command tables and
  config structs and can only say so. **Recommended: yes.**
- **D2. Record `&x` inside a file-scope initializer** (`static_configs` storing `&server.config_hz`) as an
  `escapes` row with `function_id = ''` and `detail = "address stored in the initializer of static_configs"`.
  Same table and format, the column's meaning widens (ADR 0007 addendum). Without it,
  `redisServer::config_hz` is missing its main writer path with no warning. **Recommended: yes.**
- **D3. Default depth**: 2 for globals, 0 for fields (above). Alternative: 2 for both.
- **D4. `--from FUNCTION`** (value question) in the CLI and MCP now, or only as an API for Part C.
  Recommended: both (small, and it is how "probable" gets tested).

## Bugs found while planning (issues opened, not fixed here)

- #18 Escapes: an array element passed by value (`close(arr[0])`) is recorded as the array's address
  escaping.
- #19 Fields of anonymous structs/unions have a machine path in their qualified name.
