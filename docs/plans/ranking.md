# Plan: ranked, capped signature answers

Work item 1 ("ranking"). Branch `feat/ranking`, based on `launch-candidate`. Revised after review (see
"Review" at the end); the examples below are real output on Redis 8.10.3.

## Goal

`aerotrace signature addReply` on Redis 8.10.3 printed 116 lines (CLI cap 50 per list; MCP 40), most of
them long, with call sites in file order. An agent reads the first screen and stops. The answer should
start with a summary, list the most important call sites first, group far callers by how they are
reached, and stay short by default, while saying exactly what it left out.

Scope: `signature` (CLI) and `signature_impact` (MCP). Ranked order and short caps apply to the **text**
answer. JSON stays backward compatible (see Flags). `impact` and `diff` are follow-ups.

## Output format (text)

### addReply (void, 247 call sites): 44 lines (was 116; `--all` gives 621)

```
Signature impact of addReply (src/networking.c:606): void addReply(client *, robj *), 2 parameters.
A change to its parameters or return type affects 247 call sites in 126 functions, 0 places that use its address, and 0 pointer calls that may reach it.
Summary: 247 call sites in 34 files (247 exact, 0 probable, 0 unknown); 0 do not compile now; 0 pointer calls (probable); 357 callers further up, 0 of them only through pointers.
It returns void, so no caller uses a return value.
Call sites per file, most first: src/debug.c 30, src/networking.c 21, src/db.c 15, ... (every file, one line)
Call sites (247), most important first:
  - aclCommand  src/acl.c:2962
  ... (15 rows)
  ... and 232 more call sites (use --all to see every one, or --limit N).
Callers further up (357, 2 more steps), grouped by the caller of addReply they come through. Each is listed once:
  357 callers, reached through its direct call sites; 24 of them probable:
    - backupStatusCommand  src/aof.c:3768 via addReplyLongLong
    ... (10 rows)
    ... and 347 more in this group (use --all to see every one, or --limit N).
[via_pointer] = a call through a function pointer of the matching type
Limits of this answer:
  ...
```

### setCommand (no direct sites, reached through pointers): 35 lines (was 33)

```
Summary: 0 call sites in 0 files (0 exact, 0 probable, 0 unknown); 0 do not compile now; 2 pointer calls (probable); 11 callers further up, 11 of them only through pointers.
...
Calls through a pointer of its type that may reach it (2) [probable]:
  - loadSingleAppendOnlyFile  src/aof.c:1993 [probable]
  - call  src/server.c:4058 [probable]
Callers further up (11, 2 more steps), grouped by the caller of setCommand they come through. Each is listed once:
  5 callers, reached through the pointer call in loadSingleAppendOnlyFile() at src/aof.c:1993 [probable]:
    - loadAppendOnlyFiles  src/aof.c:2113 [via_pointer] via loadSingleAppendOnlyFile
    ...
  6 callers, reached through the pointer call in call() at src/server.c:4058 [probable]:
    - RM_Call  src/module.c:6859 [via_pointer] via call
    - processCommand  src/server.c:4467 [via_pointer] via call
    ...
    ... and 1 more in this group (use --all to see every one, or --limit N).
```

### Non-void (createStringObject, 139 sites): 44 lines (was 116)

`How callers use the return value: 139 use it (131 stored, 3 passed on, 5 returned).` Pointer calls get
their own line (`How pointer calls use it: ...`). If every call ignores it: `all ignore it (...)`.

## Ranking rules

Call sites (direct, and separately the pointer call sites), in `SignatureImpact.ranked()`:

1. `does_not_compile`: the argument count does not match.
2. `uses_result`: stored, compared, checked, passed, returned, used, called.
3. `ignores_result`: ignored, or discarded with `(void)`.
4. `no_result`: every call of a void function (never called "ignores").
5. Inside a group: exact, probable, unknown; then file, line, column, caller id.

Callers further up, in `signature._groups()`, grouped by **entry**: the distance-1 function (a caller of
the target) their path comes through.

- A distance-1 function that calls the target directly at least once is a direct entry, even if it also
  calls it through a pointer. All direct entries form one group ("reached through its direct call
  sites"); probable members are tagged and counted in the header.
- A distance-1 function that reaches the target only through a pointer is a pointer entry: one group
  each. The header names the function and every pointer-call line of that type in it.
- Entries are found from the call edges between distance d-1 and d (not from the `via` name alone), so
  ties are seen. Among the walk's shortest, strongest paths, a direct entry wins, then the lowest entry
  id. Each caller is in exactly one group; if it is also reached through other groups at the same
  distance, the line says `(also reached through N other callers of <target>)`.
- Group order: direct group, then pointer groups by entry file, first line, entry id. Members nearest
  first (today's order).

## Caps (text default)

| List | Cap | Never cut |
|---|---|---|
| Call sites | 15 | calls that do not compile (always shown, on top of the 15) |
| Places that use the address | 10 | |
| Pointer call sites | 10 | pointer calls that do not compile |
| Direct group members | 10 | the group header and its count |
| Members per pointer group | 5 | the group header and its count |
| Group headers | 10 (or `--limit` if bigger) | "... and N more groups (M callers)" |

Never cut: the summary (counts, exact/probable/unknown split, not-compiling count, pointer counts even
when 0), the per-file line (every file), the use splits, the legends, and "Limits of this answer". Every
capped list ends with "... and N more ... (use --all to see every one, or --limit N)"; in MCP the hint is
"call again with show_all=true, or a bigger limit". Measured: 35 to 44 lines on the Redis and SQLite
examples (was 33 to 116).

## Flags

- CLI `aerotrace signature`: `--limit N` (N >= 1) sets every list cap to N; `--all` removes the caps;
  using both is an error. With neither, the caps above. This lowers the text default from 50 per list,
  which is the default change this item is approved for.
- MCP `signature_impact(symbol, depth=2, limit=None, show_all=False)`: same meaning. Schema change:
  `limit` is now optional (was `40`) and `show_all` is new; `limit < 1` gives a short error.
- JSON (`--json`), coordinator decision: backward compatible. `call_sites`, `upstream` and the other
  lists keep today's order and today's default cap of 50 (`--limit` and `--all` work as above). Only new
  keys: `rank_group` per site, `*_total` and `*_omitted` per list, `not_compiling_call_sites` (never
  capped), `upstream_groups` (kind, entry, pointer-call lines, total, capped members with
  `also_reached_through`, `members_omitted`), `summary` (counts, `by_certainty`, `not_compiling`,
  `direct_uses`, `pointer_uses`, `call_sites_per_file`). `return_value_uses` keeps its meaning (direct
  and pointer calls together).

## Files changed

- `src/aerotrace/analysis/signature.py`: rank groups, `ranked()`, `files()`, `uses(sites)`,
  `UpstreamGroup`, `_groups()`.
- `src/aerotrace/analysis/render.py`: new `signature_text` layout and caps, new JSON keys.
- `src/aerotrace/cli.py`: `--all`, `--limit` (>= 1, default none). `src/aerotrace/mcp/server.py`:
  `limit=None`, `show_all`.
- No change to `graph.py` (no `Hit.via_id` needed) and no index change.
- Tests: `tests/fixtures/c_rank/`, `tests/test_ranking.py`, one MCP test, 3 Redis golden facts with a
  small new check (`ranked`, `groups`, `max_lines`, `text_must_not` in `facts.py` / `test_golden.py`).

## Test plan

Fixture `tests/fixtures/c_rank/` (written to trip it up):

- `f` defined in `src/a_first.c` and again in `dup/f.c`, so calls from other files are ambiguous
  (probable). `z_last.c` (sorts last) has the only call that does not compile: it must come first.
  A probable use must come before an exact ignore; exact before probable inside a group; `(void)` ranks
  with "ignores". 20 ignored calls in `many()` trigger the cap of 15.
- `top()` calls `direct_caller()` (calls f) and `dispatch1()` (pointer call): direct group, once, exact,
  "also reached through 1". `top2()` reaches f only through `dispatch1()` and `dispatch2()` at the same
  distance: dispatch1 group (lowest entry id), never exact, "also 1". `deep()` (distance 3) follows top2.
  `calls_both()` comes through `both_ways()`, which calls f directly and through a pointer: direct, exact.
  `dispatch2()` has two pointer-call lines (both in the header) and 6 callers (cap 5, "1 more").
- `note()` is void: rank group `no_result`, the text never says "ignore".
- Every "N more" equals total minus shown, for every list, at the default and at `--limit 1`/`2`.
  Group headers beyond the cap are counted. Calls that do not compile survive `--limit 1` (text and JSON).
- CLI `--all`, `--limit 3`, `--all --limit 3` (error), `--limit 0` (error), `--json --all`. MCP default,
  `show_all`, `limit=0`. JSON keeps today's order and cap. Same answer twice, byte for byte.

Golden facts (Redis 8.10.3, proof lines copied from the source):

- `redis-ranking-getGenericCommand-uses-before-ignored`: the three `== C_ERR` calls (t_string.c:100,
  558, 569) come before the ignored call in `getCommand` (476).
- `redis-ranking-setCommand-pointer-groups`: `processCommand` (server.c:4844 `call(c,flags);`) and
  `execCommand` (multi.c:229) are in the group of the pointer call in `call()` at server.c:4058, as
  via_pointer; `loadAppendOnlyFiles` (aof.c:2152) in the group of `loadSingleAppendOnlyFile` (aof.c:1993).
  Must not: `processTimeEvents`, which calls `te->timeProc` (ae.c:335), an `aeTimeProc` (ae.h:47, 63),
  not a `redisCommandProc` (server.h:2959, 3073).
- `redis-ranking-addReply-void-and-short`: `void addReply(...)` (networking.c:606); the default answer
  is at most 60 lines, never says "ignore", and shown + "N more" equals the number of call sites.

## Risks

- A caller reachable through several entries is listed once; the "(also reached through N ...)" note
  says so. The group header says "reached through", not "only through".
- Lower text caps hide rows that were shown before. The summary, the per-file line, exact "N more"
  counts and the flag hint make up for it; calls that do not compile are never hidden.
- The direct group can hold callers whose path has a pointer step further up (tagged `[via_pointer]`,
  counted in the header as probable).

## Follow-ups (default changes need the owner)

- `impact` and `diff` text: summary first, group far callers by the pointer that brings them in, shorter
  default cap, `--all`. `signature._groups()` can be reused.
- `callers` / `callees` say "raise `limit`"; they could name the flag like signature does.

## Review

An independent reviewer read the first plan. Findings and what changed:

1. Must-fix: the `both()` fixture tested nothing (distance-1 callers are call sites, not upstream).
   Done: `top()` (direct_caller + dispatch1) and `top2()` (dispatch1 + dispatch2 tie), plus `deep()`.
2. Must-fix: group by entry function, follow paths back to distance 1, list every pointer-call line,
   a function calling both ways is direct. Done in `_groups()`; entries are computed from the call edges
   of each step (this also finds ties), so `Hit.via_id` was not needed. Tested (`calls_both`, dispatch2
   lines 7, 8).
3. Must-fix: the upstream cap must never hide groups. Done: every header with its count, up to 10
   headers, then "... and N more groups (M callers)"; 10 direct, 5 per pointer group. Tested.
4. Must-fix: calls that do not compile are never cut (text and JSON); the summary always gives the
   broken count, exact/probable/unknown split and pointer counts; limits and legends never cut. Done and
   written down under Caps. Tested with `--limit 1`.
5. Should-fix: void calls are `no_result`. Done, tested.
6. Should-fix: separate use splits for direct and pointer calls; JSON `return_value_uses` unchanged.
   Done (`direct_uses`, `pointer_uses` in `summary`).
7. Coordinator decision: JSON backward compatible (old order, cap 50, only new keys); ranked order for
   text. Done, tested.
8. Should-fix: one line with every file and its count. Done.
9. Should-fix: "(also reached through N other ...)". Done, tested.
10. Should-fix: measure real line counts before fixing the budget; test "N more = total - shown" for
    every list. Measured (above); the test covers call sites, pointer calls, addresses and groups.
11. Nit: one direct group, probable members tagged. Done (header counts them).
12. Nit: reject `--limit < 1`; mention the MCP schema change. Done (CLI error, MCP message; in the PR).
13. Nit: proof for the processTimeEvents must-not. Done (ae.h:47, 63; server.h:2959, 3073; ae.c:335).
14. Nit: deterministic group order; byte-identical output twice. Done, tested.
- The mocked example had setCommand at the wrong line; the examples are now real output
  (t_string.c:435).
