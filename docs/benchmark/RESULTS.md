# Accuracy benchmark: results (checkpoint 2, for owner review)

Run `bench/results/2026-11-24-c7df917/` on an Apple M1 Pro (macOS 26.7.2, arm64). AeroTrace
0.1.0.dev0 at commit `d39d857` (includes #55 and #56). 30 cases on Redis 8.10.3, SQLite 3.54.1 and curl
8.23.0: 9 easy, 18 hard, 3 "might break". Cases: `bench/cases/CASES.md`. Rules, written before any
tool ran: `accuracy-cases-protocol.md`. Exact setup: `bench/truth/README.md`.

**Not in this checkpoint: token counts.** They need Anthropic credentials, which this machine doesn't
have. Every token column says `n/a` until they're counted (`bench/truth/count_tokens.py`).

## Headline

**Easy cases (9):** every tool finds the breaks. AeroTrace and clangd find 25 of 26 break sites, and
cscope and ripgrep find 26 of 26. AeroTrace has 1 false alarm, clangd 4, cscope and ripgrep 11 each.

**Hard cases (18):** AeroTrace finds fewer breaks than the other three tools, with the fewest false
alarms. AeroTrace finds 91 of 439 break sites with 0 false alarms. clangd, cscope and ripgrep each find
99, with 10, 74 and 88 false alarms. One case, curl-09 (a macro), holds 344 of the 439 sites and every
tool misses most of it. Without curl-09: AeroTrace 91 of 95, the others 95 of 95.

**In one line:** in this run, AeroTrace's edge is **few false alarms, not more breaks found**.
clangd's find-references comes close on false alarms, finds slightly more, and answers in milliseconds
where AeroTrace takes 0.3 to 6.3 seconds.

## Where AeroTrace wins, ties and loses

- **Wins: false alarms.** Over the 27 build-break cases, AeroTrace's claimed breaks hold 1 false alarm.
  clangd's has 14, cscope's 85, ripgrep's 99. The text tools list every mention:
  prototypes, same-named struct members, `extern`s, and (sqlite-04) 40-plus lines in SQLite's
  extension-API macros. AeroTrace also says which places break and which are only affected, which the
  other tools don't try to do.
- **Ties: direct calls, field renames, globals, simple macros.** 21 of the 27 build cases end in a tie
  on breaks found (every tool finds them all), including every easy SQLite and Redis case and
  redis-04, sqlite-04 (a function stored in tables, 17 and 19 sites).
- **Losses (breaks AeroTrace misses that other tools find):**
  - **A removed function still named in a table** (redis-05, curl-05). The build fails: at link time in
    Redis (the command table in `commands.def`) and at compile time in curl (a filter table). AeroTrace
    says "None is sure to break". **This is the worst kind of answer by our own rule 5** (a wrong "safe").
    It is a product bug, not a benchmark choice.
  - **An integer field turned into a pointer** (redis-07, curl-07). AeroTrace lists the use as AFFECTED,
    not BREAKS, so its claimed breaks miss it. Its whole answer does include it.
  - **Macros used inside other macros** (curl-09). `Curl_trc_is_verbose` gets a new parameter, and the
    344 places that break are expansions of other trace macros that call it. AeroTrace finds 0. clangd,
    cscope and ripgrep find 4 (the direct uses). Every tool is far off here; AeroTrace is furthest.
  - **A call inside an unused `UNITTEST` function** (curl-01, `dynhds_set`): missed by AeroTrace **and**
    clangd, found by cscope and ripgrep.
- **Time:** AeroTrace takes 0.3 to 6.3 seconds per check (median 1.0 s), most of it parsing the
  changed files with libclang. The others take 1 to 81 ms. One-off setup per repo: AeroTrace index 4.8 to
  5.6 s, clangd background index 1 to 5 s, cscope database under 1 s, ripgrep none.
- **Might-break cases (3):** every tool finds all 4 hand-checked sites. AeroTrace adds 140 more AFFECTED
  places. They're callers further up the chain, which are real but outside the "direct callers only"
  rule every tool is held to, so they count as false alarms.
- **The 15-item text cap** of the MCP `check` tool hides 2, 10 and 4 claimed breaks in redis-04, redis-06
  and sqlite-04. The text says "N more", and recall here is scored on the full JSON answer. An agent that
  reads only the text sees fewer.

## Limits of this run

- One machine and one compiler (Apple clang 21, arm64). The truth would differ a little on Linux or gcc;
  issue #66 re-runs it there.
- 30 cases, drawn by seed from 10 fixed kinds of edit made by us. They are not changes from the projects'
  history (issue #65 adds some as an extra). Most cases have few break sites (1 to 25, and 344 for curl-09), which suits grep.
- "Breaks" means compile and link errors only. Tests, runtime behaviour and warnings are not checked.
- The truth names breaks by function ("site"). A tool that points at the right line in a function it
  names wrongly is scored by the function.
- clangd was asked at each symbol's definition, which a human or agent first has to find. AeroTrace reads
  the patch and needs no symbol. That convenience isn't scored.
- AeroTrace's whole answer (every AFFECTED and UNKNOWN node) counts as false alarms beyond the breaks. It
  is shown for completeness. The claimed-breaks row is the fair comparison.

## Fixes made during the run

Three fixes to how answers and truth are named, applied to every tool and logged in the protocol:
NO EFFECT FOUND nodes are left out of AeroTrace's whole answer; link errors are sited where the source
uses the missing symbol, not where the linker says after inlining; tables after an inline struct type are
recognized. The truth was rebuilt after each one, and no tool was run again.

## Bugs found in AeroTrace (for the owner; not fixed here, analysis code belongs to other branches)

1. **Removed function still referenced from a table reported as "None is sure to break"** (redis-05,
   curl-05). Expected: BREAKS at the table (compile error when static, link error otherwise).
2. **Integer field changed to a pointer type reported as AFFECTED, not BREAKS** (redis-07, curl-07).
   Clang rejects int-to-pointer conversion, so those uses don't build.
3. **No macro impact** (curl-09): a changed function-like macro reports nothing at its uses, or at
   uses through other macros.
4. **A call in an unused static (`UNITTEST`) function is not seen** (curl-01).
5. **The 15-item text cap can hide claimed breaks** (3 cases). Consider never capping BREAKS.

## Numbers

**Easy cases, all repos** (9 cases, 26 break sites)

| tool | found | recall | false alarms | median time | tokens |
|---|---|---|---|---|---|
| AeroTrace (claimed breaks) | 25/26 | 96% | 1 | 1.130 s | n/a |
| AeroTrace (whole answer) | 25/26 | 96% | 1196 | same | same |
| clangd refs | 25/26 | 96% | 4 | 0.002 s | n/a |
| cscope -L0 | 26/26 | 100% | 11 | 0.008 s | n/a |
| ripgrep -w | 26/26 | 100% | 11 | 0.025 s | n/a |

**Hard cases, all repos** (18 cases, 439 break sites)

| tool | found | recall | false alarms | median time | tokens |
|---|---|---|---|---|---|
| AeroTrace (claimed breaks) | 91/439 | 21% | 0 | 1.048 s | n/a |
| AeroTrace (whole answer) | 93/439 | 21% | 2311 | same | same |
| clangd refs | 99/439 | 23% | 10 | 0.004 s | n/a |
| cscope -L0 | 99/439 | 23% | 74 | 0.009 s | n/a |
| ripgrep -w | 99/439 | 23% | 88 | 0.024 s | n/a |

**Easy and hard together (build breaks only)** (27 cases, 465 break sites)

| tool | found | recall | false alarms | median time | tokens |
|---|---|---|---|---|---|
| AeroTrace (claimed breaks) | 116/465 | 25% | 1 | 1.048 s | n/a |
| AeroTrace (whole answer) | 118/465 | 25% | 3507 | same | same |
| clangd refs | 124/465 | 27% | 14 | 0.003 s | n/a |
| cscope -L0 | 125/465 | 27% | 85 | 0.008 s | n/a |
| ripgrep -w | 125/465 | 27% | 99 | 0.024 s | n/a |

**Might-break cases (hand-checked truth)** (3 cases, 4 break sites)

| tool | found | recall | false alarms | median time | tokens |
|---|---|---|---|---|---|
| AeroTrace (claimed breaks) | 4/4 | 100% | 140 | 0.628 s | n/a |
| AeroTrace (whole answer) | 4/4 | 100% | 140 | same | same |
| clangd refs | 4/4 | 100% | 0 | 0.002 s | n/a |
| cscope -L0 | 4/4 | 100% | 2 | 0.008 s | n/a |
| ripgrep -w | 4/4 | 100% | 4 | 0.022 s | n/a |

**redis, easy** (3 cases, 10 break sites)

| tool | found | recall | false alarms | median time | tokens |
|---|---|---|---|---|---|
| AeroTrace (claimed breaks) | 10/10 | 100% | 1 | 1.140 s | n/a |
| AeroTrace (whole answer) | 10/10 | 100% | 911 | same | same |
| clangd refs | 10/10 | 100% | 0 | 0.002 s | n/a |
| cscope -L0 | 10/10 | 100% | 1 | 0.008 s | n/a |
| ripgrep -w | 10/10 | 100% | 1 | 0.025 s | n/a |

**redis, hard** (6 cases, 48 break sites)

| tool | found | recall | false alarms | median time | tokens |
|---|---|---|---|---|---|
| AeroTrace (claimed breaks) | 46/48 | 96% | 0 | 1.474 s | n/a |
| AeroTrace (whole answer) | 47/48 | 98% | 1635 | same | same |
| clangd refs | 48/48 | 100% | 4 | 0.003 s | n/a |
| cscope -L0 | 48/48 | 100% | 7 | 0.008 s | n/a |
| ripgrep -w | 48/48 | 100% | 8 | 0.024 s | n/a |

**sqlite, easy** (3 cases, 5 break sites)

| tool | found | recall | false alarms | median time | tokens |
|---|---|---|---|---|---|
| AeroTrace (claimed breaks) | 5/5 | 100% | 0 | 3.685 s | n/a |
| AeroTrace (whole answer) | 5/5 | 100% | 147 | same | same |
| clangd refs | 5/5 | 100% | 0 | 0.078 s | n/a |
| cscope -L0 | 5/5 | 100% | 1 | 0.008 s | n/a |
| ripgrep -w | 5/5 | 100% | 1 | 0.015 s | n/a |

**sqlite, hard** (6 cases, 37 break sites)

| tool | found | recall | false alarms | median time | tokens |
|---|---|---|---|---|---|
| AeroTrace (claimed breaks) | 37/37 | 100% | 0 | 5.386 s | n/a |
| AeroTrace (whole answer) | 37/37 | 100% | 325 | same | same |
| clangd refs | 37/37 | 100% | 0 | 0.078 s | n/a |
| cscope -L0 | 37/37 | 100% | 48 | 0.010 s | n/a |
| ripgrep -w | 37/37 | 100% | 61 | 0.016 s | n/a |

**curl, easy** (3 cases, 11 break sites)

| tool | found | recall | false alarms | median time | tokens |
|---|---|---|---|---|---|
| AeroTrace (claimed breaks) | 10/11 | 91% | 0 | 0.659 s | n/a |
| AeroTrace (whole answer) | 10/11 | 91% | 138 | same | same |
| clangd refs | 10/11 | 91% | 4 | 0.001 s | n/a |
| cscope -L0 | 11/11 | 100% | 9 | 0.008 s | n/a |
| ripgrep -w | 11/11 | 100% | 9 | 0.029 s | n/a |

**curl, hard** (6 cases, 354 break sites)

| tool | found | recall | false alarms | median time | tokens |
|---|---|---|---|---|---|
| AeroTrace (claimed breaks) | 8/354 | 2% | 0 | 0.692 s | n/a |
| AeroTrace (whole answer) | 9/354 | 3% | 351 | same | same |
| clangd refs | 14/354 | 4% | 6 | 0.002 s | n/a |
| cscope -L0 | 14/354 | 4% | 19 | 0.009 s | n/a |
| ripgrep -w | 14/354 | 4% | 19 | 0.029 s | n/a |

### Every case

`found/breaks, N fa` (fa = false alarms)

| case | kind | level | breaks | AeroTrace | AeroTrace whole | clangd | cscope | ripgrep |
|---|---|---|---|---|---|---|---|---|
| redis-01 | add_param | easy | 8 | 8/8, 0 fa | 8/8, 881 fa | 8/8, 0 fa | 8/8, 0 fa | 8/8, 0 fa |
| redis-02 | remove_fn | easy | 1 | 1/1, 1 fa | 1/1, 15 fa | 1/1, 0 fa | 1/1, 1 fa | 1/1, 1 fa |
| redis-03 | int_to_void | easy | 1 | 1/1, 0 fa | 1/1, 15 fa | 1/1, 0 fa | 1/1, 0 fa | 1/1, 0 fa |
| redis-04 | ptr_param | hard | 17 | 17/17, 0 fa | 17/17, 594 fa | 17/17, 0 fa | 17/17, 0 fa | 17/17, 0 fa |
| redis-05 | ptr_remove | hard | 1 | 0/1, 0 fa | 0/1, 142 fa | 1/1, 0 fa | 1/1, 1 fa | 1/1, 1 fa |
| redis-06 | field_rename | hard | 25 | 25/25, 0 fa | 25/25, 88 fa | 25/25, 0 fa | 25/25, 0 fa | 25/25, 1 fa |
| redis-07 | field_type | hard | 1 | 0/1, 0 fa | 1/1, 802 fa | 1/1, 4 fa | 1/1, 4 fa | 1/1, 4 fa |
| redis-08 | global_rename | hard | 2 | 2/2, 0 fa | 2/2, 9 fa | 2/2, 0 fa | 2/2, 2 fa | 2/2, 2 fa |
| redis-09 | macro_arity | hard | 2 | 2/2, 0 fa | 2/2, 0 fa | 2/2, 0 fa | 2/2, 0 fa | 2/2, 0 fa |
| redis-10 | behaviour | might_break | 1 | 1/1, 21 fa | 1/1, 21 fa | 1/1, 0 fa | 1/1, 0 fa | 1/1, 0 fa |
| sqlite-01 | add_param | easy | 2 | 2/2, 0 fa | 2/2, 5 fa | 2/2, 0 fa | 2/2, 0 fa | 2/2, 0 fa |
| sqlite-02 | remove_fn | easy | 1 | 1/1, 0 fa | 1/1, 123 fa | 1/1, 0 fa | 1/1, 0 fa | 1/1, 0 fa |
| sqlite-03 | int_to_void | easy | 2 | 2/2, 0 fa | 2/2, 19 fa | 2/2, 0 fa | 2/2, 1 fa | 2/2, 1 fa |
| sqlite-04 | ptr_param | hard | 19 | 19/19, 0 fa | 19/19, 73 fa | 19/19, 0 fa | 19/19, 41 fa | 19/19, 47 fa |
| sqlite-05 | ptr_remove | hard | 1 | 1/1, 0 fa | 1/1, 50 fa | 1/1, 0 fa | 1/1, 1 fa | 1/1, 1 fa |
| sqlite-06 | field_rename | hard | 2 | 2/2, 0 fa | 2/2, 135 fa | 2/2, 0 fa | 2/2, 3 fa | 2/2, 9 fa |
| sqlite-07 | field_type | hard | 1 | 1/1, 0 fa | 1/1, 67 fa | 1/1, 0 fa | 1/1, 0 fa | 1/1, 0 fa |
| sqlite-08 | global_rename | hard | 1 | 1/1, 0 fa | 1/1, 0 fa | 1/1, 0 fa | 1/1, 1 fa | 1/1, 2 fa |
| sqlite-09 | macro_arity | hard | 13 | 13/13, 0 fa | 13/13, 0 fa | 13/13, 0 fa | 13/13, 2 fa | 13/13, 2 fa |
| sqlite-10 | behaviour | might_break | 1 | 1/1, 94 fa | 1/1, 94 fa | 1/1, 0 fa | 1/1, 1 fa | 1/1, 3 fa |
| curl-01 | add_param | easy | 5 | 4/5, 0 fa | 4/5, 24 fa | 4/5, 0 fa | 5/5, 4 fa | 5/5, 4 fa |
| curl-02 | remove_fn | easy | 5 | 5/5, 0 fa | 5/5, 10 fa | 5/5, 0 fa | 5/5, 1 fa | 5/5, 1 fa |
| curl-03 | int_to_void | easy | 1 | 1/1, 0 fa | 1/1, 104 fa | 1/1, 4 fa | 1/1, 4 fa | 1/1, 4 fa |
| curl-04 | ptr_param | hard | 2 | 2/2, 0 fa | 2/2, 131 fa | 2/2, 0 fa | 2/2, 0 fa | 2/2, 0 fa |
| curl-05 | ptr_remove | hard | 1 | 0/1, 0 fa | 0/1, 111 fa | 1/1, 0 fa | 1/1, 0 fa | 1/1, 0 fa |
| curl-06 | field_rename | hard | 4 | 4/4, 0 fa | 4/4, 17 fa | 4/4, 0 fa | 4/4, 0 fa | 4/4, 0 fa |
| curl-07 | field_type | hard | 1 | 0/1, 0 fa | 1/1, 86 fa | 1/1, 4 fa | 1/1, 4 fa | 1/1, 4 fa |
| curl-08 | global_rename | hard | 2 | 2/2, 0 fa | 2/2, 6 fa | 2/2, 0 fa | 2/2, 1 fa | 2/2, 1 fa |
| curl-09 | macro_arity | hard | 344 | 0/344, 0 fa | 0/344, 0 fa | 4/344, 2 fa | 4/344, 14 fa | 4/344, 14 fa |
| curl-10 | behaviour | might_break | 2 | 2/2, 25 fa | 2/2, 25 fa | 2/2, 0 fa | 2/2, 1 fa | 2/2, 1 fa |
