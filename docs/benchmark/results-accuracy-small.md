# Small accuracy check (callers and field users)

**Headline:** on 25 sampled functions and 10 sampled struct fields, AeroTrace's direct callers matched
the hand-built truth on 73 of 73 (precision 1.0, recall 1.0; 70 labelled `exact`, 3 `via_pointer`), and
its field users matched on 30 of 30 (precision 1.0, recall 1.0, all `exact`). There were 0 false alarms
and 1 miss, which is in the read/write split: a `x++` is not counted as a read (#52).

This is a cheap first check, not the full Part A of [protocol.md](protocol.md). The sample is small
and the truth was built by an AI agent. Read "Limits" before quoting the numbers.

## Method

- **Code:** pinned Redis 8.10.3 and SQLite 3.54.1 amalgamation, unpacked by `tests/realrepo/repos.py`
  (which also writes `compile_commands.json`). Each was indexed once with `aerotrace index`. AeroTrace
  0.1.0.dev0 at `launch-candidate` a52059f.
- **Seed:** items are ordered by `sha256("aerotrace-bench-small-1/" + id)` and taken from the top.
- **Functions** (`bench/accuracy/sample.py`): every function definition found by a simple regex and brace
  count (`bench/accuracy/cfuncs.py`) in the compiled `.c` files (Redis: `REDIS_SERVER_OBJ`; SQLite:
  `sqlite3.c`, `shell.c`), as file-qualified ids. 15 Redis and 10 SQLite. A function whose definition is
  not compiled in the index's build (checked with `cc -E` and the same flags, `bench/accuracy/live.py`)
  was skipped: 1 Redis, 17 SQLite (mostly FTS3/FTS5/R-Tree, which the default amalgamation leaves out). A
  cap of 40 name mentions was set but skipped no function.
- **Fields:** every field of `struct redisServer`, `client`, `redisObject` (Redis) and `Parse`, `Vdbe`,
  `sqlite3` (SQLite), read mechanically from the struct definitions. 5 per repo. Skipped: 2 SQLite fields
  with more than 40 mentions (`Parse.nMem` 274, `sqlite3.lookaside` 131).
- **Truth** (`bench/results/accuracy-small/truth.json`): `bench/accuracy/candidates.py` lists every
  `grep -w` hit (functions) or `.field` / `->field` hit (fields) with the enclosing function and whether
  the line survives `cc -E`. An AI agent then read every hit and kept or dropped it by hand: comments,
  prototypes, other structs with the same field name, code under a disabled `#if`, and `assert()` in
  SQLite (where `NDEBUG` is on) were dropped; lines inside multi-line macro arguments, which `cc -E`
  moves, were checked by reading the code. The sample and the truth were committed before AeroTrace was
  asked anything (commits f15e57e, f97485d).
  One change after the tool run: for the 3 functions reached only through pointers, the place the
  pointer is called (`cmd->proc(`, `pModule->xRowid(`) was then found by grep and added as
  `pointer_callers`. The tool's `via_pointer` answers prompted that search, so treat these 3 as weaker.
- **Asking:** `bench/accuracy/ask.py` runs `aerotrace callers <name>@<file> --depth 1 --limit 1000 --json`
  and `aerotrace data <Struct>::<field> --json --all`. Raw answers: `bench/results/accuracy-small/raw/`.
  Nothing was capped (`omitted` is 0 everywhere).
- **Scoring** (`bench/accuracy/score.py`, output `score.json`): precision = matches / tool answers,
  recall = matches / truth, summed over items. "exact" = only answers labelled `exact` against
  non-pointer truth. "all" = every answer, any label, against all truth including pointer callers. For
  fields, "exact" = certainty `exact`, "all" = any certainty; readers and writers are also scored on their
  own (a truth entry marked read-and-write counts in both).

**The owner should spot-check 20% of the truth** against the source (21 of 103 entries, first by
`sha256("aerotrace-bench-small-1/check/" + item + "/" + entry)`):

- `client.repldbfd` <- `src/replication.c:closeRepldbfd`
- `src/networking.c:rewriteClientCommandVector` <- `src/cluster.c:restoreCommand`
- `redisServer.stat_total_error_replies` <- `src/networking.c:afterErrorReply`
- `src/cluster.c:countKeysInSlot` <- `src/cluster_legacy.c:clusterCommandSpecial`
- `src/networking.c:addReplySetLen` <- `src/debug.c:debugCommand`
- `redisServer.stat_total_error_replies` <- `src/module.c:moduleBlockedClientTimedOut`
- `sqlite3.c:walIndexAppend` <- `sqlite3.c:walIndexRecover`
- `redisServer.backup_incr_filename` <- `src/aof.c:backupSealCommand`
- `src/networking.c:rewriteClientCommandVector` <- `src/t_string.c:setGenericCommand`
- `src/expire.c:expireGenericCommand` <- `src/expire.c:pexpireCommand`
- `src/cluster.c:countKeysInSlot` <- `src/cluster_legacy.c:clusterClaimUnassignedSlots`
- `src/networking.c:addReplySetLen` <- `src/server.c:addReplyCommandSubCommands`
- `sqlite3.dfltLockMode` <- `sqlite3.c:sqlite3Pragma`
- `src/networking.c:addReplySetLen` <- `src/t_set.c:sunionDiffGenericCommand`
- `sqlite3.c:sqlite3PagerIsreadonly` <- `sqlite3.c:btreeBeginTrans`
- `redisServer.stat_current_cow_peak` <- `src/server.c:resetChildState`
- `src/eval.c:evalCalcFunctionName` <- `src/eval.c:evalGetCommandFlags`
- `Parse.aVnbmc` <- `sqlite3.c:sqlite3ExprAssignVarNumber`
- `redisServer.stat_total_error_replies` <- `src/server.c:resetServerStats`
- `sqlite3.dfltLockMode` <- `sqlite3.c:attachFunc`
- `src/networking.c:rewriteClientCommandVector` <- `src/t_list.c:blockingPopGenericCommand`

Also worth a look: the dropped hits (`not_compiled` and `notes` in `truth.json`), because a wrong drop
would turn a real miss into a pass.

## Numbers

| Repo | Question | Label | Matches / tool answers / truth | Precision | Recall |
|---|---|---|---|---|---|
| Redis | callers | exact | 56 / 56 / 56 | 1.0 | 1.0 |
| Redis | callers | all | 58 / 58 / 58 | 1.0 | 1.0 |
| SQLite | callers | exact | 14 / 14 / 14 | 1.0 | 1.0 |
| SQLite | callers | all | 15 / 15 / 15 | 1.0 | 1.0 |
| Redis | field users | exact = all | 23 / 23 / 23 | 1.0 | 1.0 |
| SQLite | field users | exact = all | 7 / 7 / 7 | 1.0 | 1.0 |
| Redis | field readers | all | 14 / 14 / 15 | 1.0 | 0.933 |
| Redis | field writers | all | 13 / 13 / 13 | 1.0 | 1.0 |
| SQLite | field readers | all | 7 / 7 / 7 | 1.0 | 1.0 |
| SQLite | field writers | all | 5 / 5 / 5 | 1.0 | 1.0 |

- The tool gave no `probable` or `ambiguous` answers on this sample, so "probable" has no numbers. The 3
  non-exact caller answers are `via_pointer`, and all 3 are right.
- Items with empty truth and an empty answer (correct, adding nothing to the totals): exact callers of
  `latencyCommand`, `sqlite3_rollback_hook`, `pragmaVtabRowid`; all callers of `sqlite3_rollback_hook`;
  field `sqlite3.pNextBlocked` (the field exists only under `SQLITE_ENABLE_UNLOCK_NOTIFY`; the tool says it
  is not in the index and exits 1).
- "Must not" cases the tool got right (all inside the 0 false alarms): `gcraCommand` (under
  `#ifdef ENABLE_GCRA`), `addSpaceSeparator` and `sqlite3Normalize` (under `SQLITE_ENABLE_NORMALIZE`),
  `sqlite3VdbeCreate` (only an `assert()`), `openDatabase` for `dfltLockMode` (under
  `#ifdef SQLITE_DEFAULT_LOCKING_MODE`), and the 6 `sentinelRedisInstance.slave_priority` uses in
  `src/sentinel.c` (same field name, other struct).

## Every miss and false alarm

Misses (1):

- `redisServer.stat_total_error_replies`, readers: `src/networking.c:afterErrorReply`,
  `src/networking.c:683` `server.stat_total_error_replies++;`. The tool lists the function as a writer
  only. Reason: `x++`, `--x` and `x += n` are recorded as writes, not also as reads. Filed as #52. The
  function is still in the users list, so the users and writers scores are not affected.

False alarms (0): none.

Not scored, but a stated blind spot: `src/config.c:3520` stores `&(server.slave_priority)` in the
`static_configs` table (a file-scope initializer; `CONFIG SET` writes the field through it). The tool
reports 0 writers and no escapes for `redisServer::slave_priority`. Its answer does say "Its address
stored in a file-scope initializer (&x in a table) is not recorded (#27)", so this is a known, declared
gap, not a silent one. An agent reading only "writers: 0" would still be misled.

## Limits

- **Small sample:** 25 functions, 10 fields, 103 truth entries. Most sampled functions have 1 to 4
  callers; only 2 have more than 10. With numbers this small, a perfect score is consistent with a real
  miss rate of a few percent. It says nothing about the hard cases the full protocol targets: names
  shared by many `static` functions (no stratification by name frequency here), heavy macro use, very
  common functions and fields (the 40-mention cap skipped 2 fields).
- **Truth built by an AI agent**, from grep hits, not by the owner. Grep only finds calls that spell the
  name; calls built by `##` macros or through pointers stored elsewhere would be missed by both sides.
  The pointer callers were completed after the tool run (see Method).
- **Sample from a regex**, not from `nm` as the protocol asks; functions the regex can't see (for example
  defined by a macro) could not be sampled.
- **One build configuration:** the flags in `tests/realrepo/repos.py` (Redis `MALLOC=libc`, no TLS, no
  `ENABLE_GCRA`; SQLite with no extra flags, so no FTS3/FTS5/R-Tree). Code under other `#if` settings is
  not measured.
- **Only two question kinds** (direct callers, field users). No callees, blast radius, globals or test
  impact. **No agent outcome runs** (Part B): this says nothing about whether AeroTrace helps an agent.
