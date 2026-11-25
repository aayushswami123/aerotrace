# Plan: a shorter, ranked AFFECTED list in `check`

Status: plan only, not built. Written after benchmark run 1 (`bench/results/2026-11-24-c7df917`).

## The problem, in numbers

In run 1, `check` listed 3,649 AFFECTED nodes over 30 cases. Against the benchmark's "direct callers
only" rule, they count as 3,507 false alarms overall, and 140 on the 3 might-break cases.

Where they come from (counted from the raw answers):

| distance from the change | AFFECTED nodes |
|---|---|
| 1 (direct) | 91 |
| 2 | 427 |
| 3 | 595 |
| 4 | 818 |
| 5 (the depth limit) | 1,718 |

So 97.5% are callers further up, and almost half sit at the depth limit, where the walk simply stopped.
Six cases hold 2,686 of them (redis-01: 881, redis-07: 803, redis-04: 594, redis-05: 142, sqlite-06:
135, curl-04: 131). In redis-04, redis-05 and curl-04 every one is `probable`: they come in through a
function pointer, where one pointer call links to every function of that type (Redis's `call()` reaches
all 323 command handlers).

These links are mostly real. The problem is that a reader (or an agent) has to wade through hundreds of
lines to find the few that matter, and the benchmark rightly counts them against us.

## Rules the fix must keep

- BREAKS and UNKNOWN are never trimmed, capped or grouped away.
- Every trimmed list says how many were left out and how to see them (`--all`, `--depth`, JSON).
- The JSON keeps every node; only the default text and the default ranking change.

## Proposal, in order

1. **Show direct effects by default; summarise the rest.** The default text lists BREAKS, UNKNOWN and
   AFFECTED at distance 1. Callers further up become one line per direct node they come through:
   `... and N more further up, through K functions: <name> (<count>), ...` (most first).
   `--depth N` shows more levels. Expected effect on run 1: 91 AFFECTED rows in the default text instead
   of 3,649 (to be measured in run 2, with the benchmark's own scoring).
2. **Group function-pointer fan-out by the pointer call.** Callers reached through one pointer call (the
   command table's `proc`, a filter's `destroy`) become one group line:
   `through c->cmd->proc in call() (323 handlers; probable)`. `signature` already builds these groups
   (`UpstreamGroup`); `check` would reuse them.
3. **Data readers only when the change touches a write.** "Reads data that X writes" is listed only when
   the diff changes a line in X that writes that field or global, or removes X. Fields are tracked per
   struct type, not per object, so these links are already `probable`. Today a body change anywhere in X
   lists every reader of everything X writes.
4. **Callers that use the value (later).** For a return-type change, callers that ignore the result are
   already ranked last in `signature`; `check` should do the same. Going further up only through callers
   that pass the value on (return it, store it) needs data flow and is a later step.
5. **Measure it honestly.** The benchmark page gets a third AeroTrace row, "default answer", next to
   "claimed breaks" and "whole answer", so the effect of the ranking is visible and nothing is hidden.

## Risks

- Hiding a real effect further up. Mitigation: never hide BREAKS/UNKNOWN; always print the count and the
  entry points; the JSON and `--all` keep everything.
- Step 3 could miss a reader when a write moves without its line changing (a macro). Mitigation: a
  changed macro or a line AeroTrace can't follow already makes the answer say so.

## Tests

- Fixture tests for each step, with a correct answer and a blind spot (for example a grouped pointer
  call whose group still counts a BREAKS member).
- The UI goldens change for the default text; the check-contract JSON does not lose nodes.
- Re-run benchmark run 2 and report all three rows.
