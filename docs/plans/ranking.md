# Plan: ranked, capped signature answers

Work item 1 ("ranking"). Branch `feat/ranking`, based on `launch-candidate`.

## Goal

`aerotrace signature addReply` on Redis 8.10.3 prints over 300 lines today (247 call sites, 357 upstream
callers, a cap of 50 per list on the CLI and 40 in MCP, several lists). An agent reads the first screen and
stops. The answer should start with a summary, list the most important call sites first, group far callers
by how they are reached, and stay short by default, while saying exactly what it left out.

Scope: `signature` (CLI) and `signature_impact` (MCP), text and JSON. `impact` and `diff` are follow-ups
(see the end).

## Output format (text)

Mocked examples. The counts marked (measured) come from Redis 8.10.3; file names and per-file counts are
placeholders. The PR will paste the real output.

### addReply (void, many sites)

```
Signature impact of addReply (src/networking.c:606): void addReply(client *, robj *), 2 parameters.
Summary: 247 call sites in 63 files (all exact), 0 places use its address, 0 pointer calls may reach it,
357 callers further up. It returns void, so only the argument list matters.
Most call sites: src/t_list.c (21), src/cluster.c (18), src/server.c (15), src/t_zset.c (14), src/db.c (12).
Call sites (247), first 15:
  - blpopCommand  src/t_list.c:1201
  ... (14 more lines, ordered by file and line)
  ... and 232 more call sites (use --all to see every one, or --limit N).
Callers further up (357, 2 more steps), first 15:
  Through direct call sites (357):
    Distance 2 (180):
      - processCommand  src/server.c:4500 via call
      ...
  ... and 342 more callers.
Limits of this answer:
  - addReply can be called from outside this repo ...
```

### setCommand (no direct sites, reached through pointers)

```
Signature impact of setCommand (src/t_string.c:439): void setCommand(client *), 1 parameter.
Summary: 0 direct call sites, 2 places use its address, 2 pointer calls may reach it (measured),
11 callers further up, all through pointers (measured). It returns void.
Places that use its address (2): ...
  - src/commands.def:13094  in the initializer of redisCommandTable: stored in an initializer
  - src/t_string.c:201  in setGenericCommand: compared
Calls through a pointer of its type that may reach it (2) [probable]:
  - call  src/server.c:4058 [probable]
  - loadSingleAppendOnlyFile  src/aof.c:1993 [probable]
Callers further up (11, 2 more steps), grouped by the pointer call that brings them in [probable]:
  9 callers, reached through the pointer call in call() at src/server.c:4058:
    - processCommand  src/server.c:4844 [via_pointer]
    - execCommand  src/multi.c:227 [via_pointer]
    ...
  2 callers, reached through the pointer call in loadSingleAppendOnlyFile() at src/aof.c:1993:
    - loadAppendOnlyFiles  src/aof.c:... [via_pointer]
    ...
[probable] = the link is real, but may not apply here (pointer, virtual call, same name, other object)
Limits of this answer: ...
```

### Non-void function (createStringObject, 139 sites, measured)

The summary line gives the use split, e.g. "139 call sites in N files; 120 use the result (98 stored, 22
passed on), 19 ignore it". If every site ignores the result, it says "all ignore the result". For a void
function it never says "ignore" (addReply is void; the measured "all ignored" is only how void calls are
stored).

## Ranking rules

Call sites (direct, and separately pointer call sites), sorted by this key:

1. Calls that do not compile now (argument count does not match).
2. Calls that use the result: stored, compared, checked, passed, returned, used, called.
3. Calls that ignore it: ignored, discarded with (void).
4. Inside each group: exact before probable before unknown; then file, line, column, caller id.

For void functions groups 2 and 3 collapse (every call is "ignored"), so the order is: broken, then
exact/probable, then file/line.

The rank is computed in `analysis/signature.py` (a `rank_key(site)` function and the lists are stored
already ranked), not in the renderer, so CLI, MCP and JSON agree.

Most-call-site files: top 5 files by number of direct call sites; ties by file name.

Callers further up (upstream), grouped by the distance-1 call site whose path brings them in ("entry"):

- Each upstream hit is assigned to the entry on its best path (the path `walk_many` already picks:
  shortest, then strongest). Each caller appears once.
- Group order: callers through direct exact sites; then through probable direct sites (virtual, same
  name); then one group per pointer call site, ordered by file and line.
- Direct groups are one group ("Through direct call sites"), shown by distance like today. Pointer groups
  are one group per pointer call site, named by its function, file and line, because that is the thing an
  agent must look at.
- Inside a group: distance, then confidence, file, line (today's order).

To know the entry, `Hit` gets an optional `via_id: str | None = None` (the id behind today's `via` name),
filled by `walk_many`. Names alone are not enough: two static functions can share a name. This is an
in-memory field only; no index change.

## Caps (text default)

| List | Default cap | Why |
|---|---|---|
| Call sites | 15 | Enough to see the broken and used ones first; the summary and top files tell where the rest are. |
| Places that use the address | 10 | Usually 1 to 3; tables can be long. |
| Pointer call sites | 10 | Usually 1 to 5. |
| Callers further up | 15 in total, at most 5 per pointer group | Far callers matter less than call sites; groups keep the "why" visible. |
| Most-call-site files | 5 | One line. |

Worst case is about 75 lines (header 4, sites 16, uses 1, addresses 11, pointer 11, upstream about 22,
legend 2, limits about 6), instead of 300+. Every capped list ends with "... and N more <things> (use
--all to see every one, or --limit N)". In MCP the hint says `show_all=true` instead of `--all`.

## Flags

- CLI `aerotrace signature`: `--limit N` keeps working and sets every list cap to N (as today).
  New `--all`: no caps. `--all` and `--limit` together is an error (argparse mutually exclusive group).
  With neither, the default caps above apply. This lowers the CLI default from 50 per list; that is the
  default change this item is approved for.
- MCP `signature_impact(symbol, depth=2, limit=None, show_all=False)`: same meaning. Today's default is
  40 per list; it becomes the caps above. `limit` given = today's behaviour.
- `--json`: same ranking. Caps: `--limit` and `--all` as above; with neither, keep today's 50 per list so
  scripts don't silently lose rows. New keys only (no key removed or renamed): `summary`
  (counts, use split, top files), `call_sites_omitted`, `address_sites_omitted`,
  `pointer_call_sites_omitted`, `upstream_omitted`, `upstream_groups` (entry + member ids), and each
  call site gets `rank_group` ("does_not_compile" | "uses_result" | "ignores_result").
  Note for the owner: the order of `call_sites` in JSON changes (ranked instead of certainty/file).

## Files to change

- `src/aerotrace/analysis/signature.py`: rank key, ranked lists, top files, use split, upstream groups.
- `src/aerotrace/analysis/graph.py`: `Hit.via_id` (optional, default None), set in `walk_many`.
- `src/aerotrace/analysis/render.py`: `signature_text` / `signature_json` new layout, caps, omitted
  counts, a `more_hint` argument so CLI and MCP name their own flag.
- `src/aerotrace/cli.py`: `--all`, `--limit` default `None`.
- `src/aerotrace/mcp/server.py`: `show_all`, `limit=None`; tool description mentions the cap.
- `tests/test_signature.py`, new fixture `tests/fixtures/c_rank/`, `tests/realrepo/facts_redis.py`,
  `tests/realrepo/facts.py` + `test_golden.py` (order and group checks).
- `README` / docs only where the `signature` flags are listed.

## Test plan

Fixture `tests/fixtures/c_rank/` (written to trip the ranking up):

- `int f(int a, int b)` called from `a.c` (file first alphabetically) with the result ignored, from `z.c`
  with one wrong-argument-count call (must still rank first), from `m.c` stored/compared/passed, and one
  `(void)f(...)` discard (ranks with "ignored").
- A same-name static `f` in another file so one call is `ambiguous` (probable): a probable "used" site must
  rank before an exact "ignored" site (use group beats certainty), and an exact "used" site before a
  probable "used" one.
- 20 calls in one file so the 15 cap triggers; the test checks "... and N more" equals total minus shown.
- Two pointer tables of `f`'s type called from `dispatch1()` and `dispatch2()`, each with its own callers:
  two upstream groups with the right members. One function `both()` calls `f` directly and also reaches it
  through `dispatch1()`: it must sit under the direct group, once, and be exact. A caller only reachable
  through a pointer must never be exact.
- A void function: the summary says "returns void" and never "ignore".
- `--all` shows every line; `--all` with `--limit` fails; `--limit 3` caps every list at 3; MCP
  `show_all=True` and default; JSON omitted counts and unchanged default cap of 50.
- Determinism: the same answer twice, byte for byte.

Golden facts (Redis 8.10.3, hand-checked, proof lines copied from the source):

- `redis-ranking-getGenericCommand`: the three `== C_ERR` comparisons (t_string.c:100, 558, 569) are
  listed before the ignored call in `getCommand` (t_string.c:476). Must not: the ignored site first.
- `redis-ranking-setCommand-groups`: `processCommand` (server.c:4844 `call(c,flags);`) is in the upstream
  group of the pointer call in `call()` at server.c:4058, labelled probable / via_pointer. Must not:
  `processCommand` shown as exact, or `processTimeEvents` in any group.
- `redis-ranking-addReply-void`: `void addReply(client *c, robj *obj)` (networking.c:606); the text answer
  says it returns void, does not say "ignore", is at most 80 lines by default, and its "and N more" equals
  total minus shown (a consistency check, not a copied number).
- These need a small new check kind (`order` = list of (caller, file, line) that must appear in this
  relative order; `groups` = entry -> required members). Existing facts and the 3 known failures stay as
  they are.

Runs: `uv run ruff check . && uv run ruff format --check . && uv run pytest`, then
`uv run pytest -m realrepo -rxX` (still exactly the 3 known failures, #12, #13).

## Risks

- `Hit.via_id` is shared by `impact` and `diff`. Default `None` and not added to their JSON, so their
  output is unchanged; existing tests prove it.
- A caller reachable through two pointers (or a pointer and a direct call) is shown under one entry only,
  the best path. The group header says "reached through", not "only through"; the text will say each
  caller is listed once.
- Lower default caps hide rows that were shown before. Mitigated by the summary, top files, exact omitted
  counts and the `--all` hint. A wrong "safe" reading is the risk: the summary must always give the full
  counts, including broken calls, which always rank first and are never hidden behind a cap before any
  non-broken site.
- Existing text assertions in `tests/test_signature.py` may need new expectations where the layout
  changes on purpose. No assertion is weakened; each change is listed in the PR.
- JSON `call_sites` order changes (flagged above for the owner).

## Follow-ups (not in this item; default changes need the owner)

- `impact` and `diff` text: same "summary first, group far callers by the pointer that brings them in,
  smaller default cap, --all". The group helper will be written so they can reuse it.
- `callers` / `callees`: say which flag shows more ("raise `limit`" today).
