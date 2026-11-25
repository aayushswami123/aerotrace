# Accuracy benchmark: results (checkpoint 2, for owner review)

Run `bench/results/2026-11-24-c7df917/` on an Apple M1 Pro (macOS 26.7.2, arm64). AeroTrace
0.1.0.dev0, analysis code as on `launch-candidate` at `eff898b` (Merge #56, so #55 and #56 are in). The
setup files record `d39d857`, the branch commit at run time. That commit was later rebased away; it has
no `src/` difference from `eff898b`. #71 and later merges came after this run and are not measured.

30 cases on Redis 8.10.3, SQLite 3.54.1 and curl 8.23.0: 9 easy, 18 hard, 3 "might break". Cases:
`bench/cases/CASES.md`. Rules, written before any tool ran: `accuracy-cases-protocol.md`. Exact setup:
`bench/truth/README.md`.

**Not in this checkpoint: token counts.** They need Anthropic credentials, which this machine doesn't
have. Every token column says `n/a` until they're counted (`bench/truth/count_tokens.py`).

## Headline

**Easy cases (9 cases, 25 break sites):** a tie. Every tool finds all 25. False alarms: AeroTrace 1,
clangd 4, cscope 12, ripgrep 12.

**Hard cases, without curl-09 (17 cases, 95 break sites):** AeroTrace finds 91 with **0** false alarms.
clangd, cscope and ripgrep each find all 95, with 8, 60 and 74 false alarms.

**curl-09 on its own (1 hard case, 344 break sites):** every tool misses almost all of it. AeroTrace finds
0, and the others find 4. It's shown apart because it would otherwise be 78% of the hard total (all hard
cases together: AeroTrace 91 of 439, the others 99 of 439). See "curl-09" below.

**In one line:** AeroTrace's edge in this run is **few false alarms, not more breaks found**. On hard
cases it misses 4 breaks the others find. clangd is the closest competitor: it finds every break outside
curl-09, has 8 false alarms on hard cases, and answers in milliseconds where AeroTrace takes 0.3 to 6.3
seconds.

## Where AeroTrace wins, ties and loses

- **Wins: false alarms.** Over the 27 build-break cases, AeroTrace's claimed breaks hold 1 false alarm
  (redis-02, explained below). clangd's hold 14: 8 in the two int-to-pointer field cases, 4 in curl-03
  and 2 in curl-09. cscope's hold 86, ripgrep's 100. "False alarm" here means "outside the compile-error
  truth". Some are real problems that clang only warns about. In curl-07, for example, a `void **` is
  passed where a `timediff_t *` is expected, and pointers are compared with integers. Those count against
  clangd, cscope and ripgrep, though a reader would want to see them. AeroTrace also says which places
  break and which are only affected, which the other tools don't try to do.
- **Ties:** 22 of the 27 build cases end with every tool finding every break: direct calls, field
  renames, globals, macros in `.c` files, and functions stored in tables with direct calls (redis-04 and
  sqlite-04, 17 and 19 sites).
- **Losses (breaks AeroTrace misses that the others find):**
  - **A removed function still named in a table** (redis-05, curl-05). The build fails: at link time in
    Redis (the command table in `commands.def`) and at compile time in curl (a filter table). AeroTrace
    says "None is sure to break". **That is the worst kind of answer by our own rule 5** (a wrong "safe").
    It is a product bug, not a benchmark choice.
  - **An integer field turned into a pointer** (redis-07, curl-07). AeroTrace lists every use as
    AFFECTED, and the use that fails to build isn't set apart as BREAKS. Its whole answer does include it,
    but its headline again says "None is sure to break", so these are wrong "safe" answers too.
  - **A macro changed in a header** (curl-09). See below.
- **Time:** AeroTrace takes 0.3 to 6.3 seconds per check (median 1.0 s), most of it parsing the changed
  files with libclang. The others take 1 to 81 ms. One-off setup per repo, on a cold cache: AeroTrace index
  4.8 to 5.6 s, clangd background index 0.8 to 4.8 s, cscope database under 1 s, ripgrep none.
- **Might-break cases (3):** every tool finds all 4 hand-checked sites. AeroTrace adds 140 more AFFECTED
  places. They're callers further up the chain, which are real but outside the "direct callers only"
  rule every tool is held to, so they count as false alarms.
- **The 15-item text cap** of the MCP `check` tool hides 2, 10 and 4 claimed breaks in redis-04, redis-06
  and sqlite-04 behind "N more". Recall here is scored on the full JSON answer, so an agent that reads
  only the text sees fewer.

## curl-09: why one case is far off for everyone

`Curl_trc_is_verbose` gets a new parameter. It is used inside three other `#define`s in `curl_trc.h`
(`infof` among them), and those are used all over curl. So the compiler reports 344 functions, each
"expanded from macro 'infof'" or one of the other two. Fixing the three `#define` lines would fix the
whole build.

clangd points at two of those three `#define` lines, and cscope and ripgrep at all three. That's the
most useful answer here. The rules say a `#define` line is never a break (the breaks are where the macro
is expanded), so those answers count as false alarms. AeroTrace answers "Nothing found that this change
can break, but 2 parts of the change could not be followed", so it does say what it couldn't see. The
rule was written before the run and is kept; this case is shown apart so it doesn't decide the headline.

## redis-02: AeroTrace's one easy false alarm

AeroTrace's BREAKS node `redisPopcountAuto` gives the call line as 278. That's the line number in the
**patched** file, where the patch deleted 100 lines above it. On the unchanged file, line 278 is inside
`redisPopCountAvx512`, which is under `#ifdef HAVE_AVX512` and not compiled on arm64. With the right line,
AeroTrace would have 0 false alarms here. The score is kept as its answer says. The line-numbering mix-up
is listed as a bug below.

## Limits of this run

- One machine and one compiler (Apple clang 21, arm64). The truth would differ a little on Linux or gcc;
  issue #66 re-runs it there.
- 30 cases drawn by seed from 10 fixed kinds of edit, which we made ourselves, scored by rules we wrote.
  They're not changes from the projects' history (issue #65 adds some as an extra). Most cases have few
  break sites (1 to 25, and 344 for curl-09), which suits grep.
- "Breaks" means compile and link errors only. Tests, runtime behaviour and warnings are not checked.
- The truth names breaks by function ("site"). A tool that points at the right line in a function it
  names wrongly is scored by the function.
- cscope was asked `-L0` ("find this symbol") for every kind, as fixed in the rules. For function cases,
  `-L3` ("functions calling this") would list fewer prototypes and `extern`s, so fewer false alarms.
- clangd was asked at each symbol's definition, which a human or agent first has to find. AeroTrace reads
  the patch and needs no symbol. That convenience isn't scored.
- AeroTrace's whole answer (every AFFECTED and UNKNOWN node) counts as false alarms beyond the breaks. It
  is shown for completeness. The claimed-breaks row is the fair comparison.

## Fixes made during the run

Four fixes, applied to every tool and logged in the protocol. The truth was rebuilt after each one, and
no tool was run again.
1. NO EFFECT FOUND nodes are left out of AeroTrace's whole answer.
2. Link errors are sited where the source uses the missing symbol, not in the function the linker names
   after inlining.
3. Tables after an inline struct type are recognized.
4. curl's truth was first built with the default `make`. That also builds two test-only libraries with
   `-DUNITTESTS`, code the tools never saw. It's now built from `libcurl` and the `curl` program only,
   which is the build the cases define. This removed one break site from curl-01 that every
   parser-based tool had "missed" (found in review).

## Bugs found in AeroTrace (for the owner; not fixed here, analysis code belongs to other branches)

**Wrong "safe" answers in 4 build cases:** in redis-05, redis-07, curl-05 and curl-07 the build fails, and
AeroTrace's answer says "None is sure to break". (Found while building the results page; an earlier
version of this file said 2.)

1. **A removed function still referenced from a table is reported as "None is sure to break"** (redis-05,
   curl-05). Expected: BREAKS at the table (a compile error when the function is static, a link error
   otherwise). This is a rule-5 problem.
2. **An integer field changed to a pointer type is reported as AFFECTED, not BREAKS** (redis-07,
   curl-07), with "None is sure to break" in the headline. Clang rejects int-to-pointer conversion, so
   those uses don't build. This is a rule-5 problem as well.
3. **A macro changed in a header is not followed** (curl-09). It is flagged as not followed, so this isn't
   a silent gap. Macros in `.c` files work: redis-09 and sqlite-09 find every break.
4. **Mixed line numbers:** for a node in a file the patch changes, `lines` can be patched-file numbers
   while `line` and other nodes use the unchanged file (redis-02).
5. **The 15-item text cap can hide claimed breaks** (3 cases). Consider never capping BREAKS.

## Numbers

**Easy cases, all repos** (9 cases, 25 break sites)

| tool | found | recall | false alarms | median time | tokens |
|---|---|---|---|---|---|
| AeroTrace (claimed breaks) | 25/25 | 100% | 1 | 1.130 s | n/a |
| AeroTrace (whole answer) | 25/25 | 100% | 1196 | same | same |
| clangd refs | 25/25 | 100% | 4 | 0.002 s | n/a |
| cscope -L0 | 25/25 | 100% | 12 | 0.008 s | n/a |
| ripgrep -w | 25/25 | 100% | 12 | 0.025 s | n/a |

**Hard cases without curl-09 (extra view)** (17 cases, 95 break sites)

| tool | found | recall | false alarms | median time | tokens |
|---|---|---|---|---|---|
| AeroTrace (claimed breaks) | 91/95 | 96% | 0 | 1.048 s | n/a |
| AeroTrace (whole answer) | 93/95 | 98% | 2311 | same | same |
| clangd refs | 95/95 | 100% | 8 | 0.004 s | n/a |
| cscope -L0 | 95/95 | 100% | 60 | 0.009 s | n/a |
| ripgrep -w | 95/95 | 100% | 74 | 0.022 s | n/a |

**Hard cases, all repos** (18 cases, 439 break sites)

| tool | found | recall | false alarms | median time | tokens |
|---|---|---|---|---|---|
| AeroTrace (claimed breaks) | 91/439 | 21% | 0 | 1.048 s | n/a |
| AeroTrace (whole answer) | 93/439 | 21% | 2311 | same | same |
| clangd refs | 99/439 | 23% | 10 | 0.004 s | n/a |
| cscope -L0 | 99/439 | 23% | 74 | 0.009 s | n/a |
| ripgrep -w | 99/439 | 23% | 88 | 0.024 s | n/a |

**Easy and hard together (build breaks only)** (27 cases, 464 break sites)

| tool | found | recall | false alarms | median time | tokens |
|---|---|---|---|---|---|
| AeroTrace (claimed breaks) | 116/464 | 25% | 1 | 1.048 s | n/a |
| AeroTrace (whole answer) | 118/464 | 25% | 3507 | same | same |
| clangd refs | 124/464 | 27% | 14 | 0.003 s | n/a |
| cscope -L0 | 124/464 | 27% | 86 | 0.008 s | n/a |
| ripgrep -w | 124/464 | 27% | 100 | 0.024 s | n/a |

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

**curl, easy** (3 cases, 10 break sites)

| tool | found | recall | false alarms | median time | tokens |
|---|---|---|---|---|---|
| AeroTrace (claimed breaks) | 10/10 | 100% | 0 | 0.659 s | n/a |
| AeroTrace (whole answer) | 10/10 | 100% | 138 | same | same |
| clangd refs | 10/10 | 100% | 4 | 0.001 s | n/a |
| cscope -L0 | 10/10 | 100% | 10 | 0.008 s | n/a |
| ripgrep -w | 10/10 | 100% | 10 | 0.029 s | n/a |

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
| curl-01 | add_param | easy | 4 | 4/4, 0 fa | 4/4, 24 fa | 4/4, 0 fa | 4/4, 5 fa | 4/4, 5 fa |
| curl-02 | remove_fn | easy | 5 | 5/5, 0 fa | 5/5, 10 fa | 5/5, 0 fa | 5/5, 1 fa | 5/5, 1 fa |
| curl-03 | int_to_void | easy | 1 | 1/1, 0 fa | 1/1, 104 fa | 1/1, 4 fa | 1/1, 4 fa | 1/1, 4 fa |
| curl-04 | ptr_param | hard | 2 | 2/2, 0 fa | 2/2, 131 fa | 2/2, 0 fa | 2/2, 0 fa | 2/2, 0 fa |
| curl-05 | ptr_remove | hard | 1 | 0/1, 0 fa | 0/1, 111 fa | 1/1, 0 fa | 1/1, 0 fa | 1/1, 0 fa |
| curl-06 | field_rename | hard | 4 | 4/4, 0 fa | 4/4, 17 fa | 4/4, 0 fa | 4/4, 0 fa | 4/4, 0 fa |
| curl-07 | field_type | hard | 1 | 0/1, 0 fa | 1/1, 86 fa | 1/1, 4 fa | 1/1, 4 fa | 1/1, 4 fa |
| curl-08 | global_rename | hard | 2 | 2/2, 0 fa | 2/2, 6 fa | 2/2, 0 fa | 2/2, 1 fa | 2/2, 1 fa |
| curl-09 | macro_arity | hard | 344 | 0/344, 0 fa | 0/344, 0 fa | 4/344, 2 fa | 4/344, 14 fa | 4/344, 14 fa |
| curl-10 | behaviour | might_break | 2 | 2/2, 25 fa | 2/2, 25 fa | 2/2, 0 fa | 2/2, 1 fa | 2/2, 1 fa |
