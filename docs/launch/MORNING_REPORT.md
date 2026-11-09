# Morning report: night of 2026-11-08 (interim version, updated during the night)

This is an early version, written at 00:40 so you get something even if the night stops early.
The model credentials this session runs on expire around 01:37 local time; if they don't refresh,
work stops then. The final version replaces this file.

## Rules I kept
- `main` untouched. Everything goes into `launch-candidate` through its own PR.
- Every plan and every PR got an independent reviewer agent; builders fixed findings before any merge.
- No index format change beyond version 3. No test removed or weakened.

## Merged into launch-candidate so far
- #17: calls that don't compile are still calls (#14), incl. C++ after review. Same commits as #15 (to main, waiting for you).

## Open, waiting for a gate
| PR | What | State |
|---|---|---|
| #23 | Ranking and caps for `signature` | approved; rebasing after #17 |
| #34 | Test impact part 1 (static tests) | review said changes needed; being fixed |
| #38 | Launch UI (terminal + offline HTML renderer, README draft) | in review |
| #41 | Part B: struct fields and globals (`aerotrace data`) | in review |
| #30 | Benchmark task list + protocol | review said changes needed; being fixed |
| (building) | MCP rebuild with progress and early answer | builder running |

## Not started yet (depend on the above)
Part C (`aerotrace check`), the guardrail hook (item 7), benchmark runs (item 8), screenshots.

## Decisions waiting for you
1. Benchmark harness location: a separate `bench/` script instead of an `aerotrace bench`
   subcommand, because it builds code and sends code to a model provider (product rules 2 and 6).
2. Product rule 7 says "no web UI": you asked for `aerotrace check --html` tonight. Please add the
   exception to CLAUDE.md ("static offline report file only"); I did not edit CLAUDE.md.
3. Two agents had their `git commit` blocked by the permission system (your global CLAUDE.md says
   never commit; the AeroTrace repo exception is in my memory notes). I did not commit for them;
   they retried later and it worked. You may want to make the repo exception explicit in settings.
4. The agent benchmark (120 Claude Code runs) needs ~8-10 h even run 6 at a time, and the hook and
   Part C first. It will not run tonight. Its protocol is being fixed and committed so nothing can be
   tuned later.
5. A diff hint line pointing to test impact (changes `diff` output): not done, needs you.

## Bugs found tonight (issues, not fixed silently)
#18 array element passed by value counted as escape; #19 machine path in names of anonymous
struct members; #21 C++ constructor calls that don't compile; #22 `(*f)(...)` counts as address
taken; #24 offsetof not recorded; #25 field-designator / undeclared-identifier errors not recorded;
#26 positional initializers; #27 `&x` in file-scope tables recorded nowhere (wrong safe answer);
#28 C++ file-scope dynamic initializers; #29 clang error limit cuts missing members; #31 overloaded
function as value in a template not address-taken; #32 diff misses virtual-dispatch callers when an
override's parameters change; #33 dependent calls in C++ templates not linked; #37 `{.x = 0}` inside
functions records no write; #39 broken call on an object with operator(); #40 header static inline
compiled as C and C++ gives two indistinguishable choices. Plus earlier: #12, #13, #14 (fixed in #17).
Roadmap: #16 (follow a local variable), #20 (Tcl test names).
