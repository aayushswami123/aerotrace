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
    AFFECTED, and the use that fails to build isn't set apart as BREAKS. Its whole answer does include it.
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

1. **A removed function still referenced from a table is reported as "None is sure to break"** (redis-05,
   curl-05). Expected: BREAKS at the table (a compile error when the function is static, a link error
   otherwise). This is a rule-5 problem.
2. **An integer field changed to a pointer type is reported as AFFECTED, not BREAKS** (redis-07,
   curl-07). Clang rejects int-to-pointer conversion, so those uses don't build.
3. **A macro changed in a header is not followed** (curl-09). It is flagged as not followed, so this isn't
   a silent gap. Macros in `.c` files work: redis-09 and sqlite-09 find every break.
4. **Mixed line numbers:** for a node in a file the patch changes, `lines` can be patched-file numbers
   while `line` and other nodes use the unchanged file (redis-02).
5. **The 15-item text cap can hide claimed breaks** (3 cases). Consider never capping BREAKS.

## Numbers
