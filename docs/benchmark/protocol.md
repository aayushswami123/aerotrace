# Benchmark protocol

This file fixes how the public benchmark is run, **before any run**, so nobody can tune it after seeing
results. It covers Part A (accuracy of AeroTrace's answers) and Part B (does AeroTrace help a coding agent
on real tasks). The Part B tasks are in [tasks.md](tasks.md).

Version 2 (2026-11-09): review of PR #30 fixed before any run. Version 1 was never used.

Rules that apply to everything below:

- Every run is reported, including runs where AeroTrace made things worse, runs that crashed and runs
  that timed out. Nothing is rerun to get a better number. The only reruns allowed are the ones listed under
  "Invalid runs", and the discarded runs are reported too.
- Raw logs are committed (`bench/results/<date>/`), next to the summary made from them.
- If this protocol has to change after the first run, the change is a new protocol version with a dated
  note at the top. Results under different versions are never mixed in one table.
- If a check turns out to be wrong after runs have been judged, the **original verdict stays the
  headline**. The fixed check's verdict is shown next to it, for every run of that task.

## Pending owner decision: where the harness lives

Proposed: a separate `bench/` folder, run with `uv run bench/run.py ...`. It is **not** shipped in the
package and is **not** an `aerotrace bench` subcommand. The reason is that it builds and runs third-party code
(CLAUDE.md rule 6: AeroTrace never runs a build on its own) and it sends code to a model provider through
Claude Code (rule 2: nothing leaves the machine). Part A calls the product only through its normal CLI,
which is also the honest way to measure what users get. **This needs the owner's OK.** The harness is not
written yet; its design is under "Harness design" below.

## Machine and feasibility (checked 2026-11-08/09, before any run)

| Item | Value |
|---|---|
| Machine | Apple M1 Pro, 8 cores, macOS 26.7.2, arm64 |
| Compiler | Apple clang 21.0.0 (clang-2100.1.1.101) |
| Tcl | `/usr/bin/tclsh` 8.5.9 (the Redis test suite needs 8.5 or newer) |
| Claude Code | `~/.local/bin/claude`, version 2.1.319 |
| Model access | Amazon Bedrock through the inherited environment (`CLAUDE_CODE_USE_BEDROCK`, `AWS_PROFILE`, `AWS_REGION`); a run with an empty config dir needed no login and reported `global.anthropic.claude-opus-5-5`, `provider: bedrock` (seen in review) |
| AeroTrace | 0.1.0.dev0 at `launch-candidate` 01032b7 (the version used for runs is recorded per run) |

Redis 8.10.3 (pinned tarball from `tests/realrepo/repos.py`, unpacked into a temp folder):

- `make -j8 MALLOC=libc` at the top level **exits 2**: in 8.10.3 the top-level Makefile also tries to build
  the bundled modules (redisbloom, redisearch, redisjson, redistimeseries), which fail here. `redis-server`
  itself was built fine before that. So the build command for the benchmark is
  **`make -C src -j<N> MALLOC=libc`**, which builds the server, cli, benchmark, check-rdb/aof and the test
  modules in `tests/modules`. It exits 0 with **0 compiler warnings**.
- Time with `-j8`: clean build about 35 s (38 s in review); rebuild after `touch src/server.h` about 24 s
  (link-time optimisation dominates).
- Build with C unit tests: `make -C src -j8 MALLOC=libc REDIS_CFLAGS=-DREDIS_TEST` about 27 s;
  `./src/redis-server test util` exits 0.
- Test suite works: `./runtest --single unit/type/string` passed in 16 s. Every test unit named in
  tasks.md version 1 was run once on the unchanged build and **all passed** (`--clients 8`, 39 units, about
  2.5 minutes of wall time in total). Slowest units: `unit/type/stream` 80 s, `unit/maxmemory` 61 s,
  `unit/introspection` 54 s, `unit/scripting` 50 s, `unit/dump` 44 s, `integration/rdb` 44 s. Module API
  units run directly with `./runtest --single unit/moduleapi/<name>`. We don't use `./runtest-moduleapi`,
  because it always runs all 49 module units (151 s).
- Memory leak checks in the suite use macOS `leaks` and passed.

SQLite 3.54.1 amalgamation:

- `cc -O1 -Wall -o sqlite3 shell.c sqlite3.c` exits 0 with 0 warnings in about 9 s.
- The extended compile used by task S01 (R-Tree, Geopoly, FTS3/5, session, STAT4, dbstat, `SQLITE_DEBUG`,
  `-O0 -c`) exits 0 with 0 warnings in about 3 s.
- The amalgamation has **no test suite** (SQLite's Tcl tests are not in it). We use the smoke scripts below
  instead, and the report says so.

## Check validation (done before freezing)

A check that passes when the agent does nothing, or passes a plausible wrong fix, is a broken check. For
each task, three things must hold before the first run:

1. **Empty diff fails.** Run on the unchanged sources, at least one check must fail.
2. **Reference patch passes.** A correct patch passes the diff gate, the build, the tests and every check.
3. **A plausible wrong patch fails.** At least one, the kind of mistake the task is about.

The reference and wrong patches are committed as `bench/patches/<id>/reference.patch` and `wrong-*.patch`,
and `bench/run.py validate` reruns all three for every task.

**Done so far (static checks and the listed builds only, on this machine):**

- **Empty diff, all 20 tasks:** every task has at least one failing check on the unchanged sources.
  Failing checks: R01 definition and prototype text; R02 contract test (abort, exit 134); R03 both
  prototypes; R04 the typedef and `argcount` for all 7 names; R05, R06 `argcount`; R07 still `list *`;
  R08 no `lru_or_lfu`; R09 static check (3 of 4 parts); R10 field still there; R11, R13 `idents.py` finds
  uses; R12 9 uses; R14 `idents.py` and the JSON; R15 no new file, Makefile unchanged; S01 no `nParseErr`, 6
  `nErr` fields; S02 prototype and definition order; B01 field order and `MAKE_CMD`; B02 macro and
  definition; C02 field still there. The builds and tests pass on the unchanged sources, as shown above.
- **R02:** the contract test aborts on the unchanged `util.c`. It prints `R02 contract OK` when only
  `string2ll`'s own returns are switched to 0/-1 (a contract-only patch, not a full reference patch).
- **R09:** reference patch (field `long long` plus `createLongLongConfig`) builds with exit 0 and 0
  warnings and passes the static check. The reviewer's wrong patch (`*(int *)&server.maxmemory_samples`
  in the `createIntConfig` line) builds with exit 0 and **0 warnings**, so the old checks passed it. The
  new static check fails it (3 of 4 parts fail).
- **R13:** `idents.py src lookupKeyRead` lists 52 places on the unchanged sources (definition, prototype,
  50 calls) and ignores the 3 comments that `grep -w` matched (`db.c:250`, `hyperloglog.c:1738`, `:1740`).
- **B02:** wrong patch (function and declaration changed, macro and callers not): exit 0, 0 warnings, and
  the `SQLITE_DEBUG` file smoke test aborts on `assert( rc==SQLITE_CANTOPEN )` in `unixSync`. Reference patch:
  exit 0, 0 warnings, both file smoke runs pass.

**Still to do before the first run (a gate: no run starts until it is done and committed):**

- Reference and wrong patches for every task. R01, R02, R03, R09, R13 and S01 come first.
- For R01, R02 and R03: on top of the reference patch, undo one caller at a time, then build and run the
  task's tests. Record which ones are caught. Callers no test catches are listed in the report as
  "not covered by tests"; we don't assume the tests catch them.
- The flake rate of every task's test line (see "Flaky tests").

## Part B: agent outcomes

### Arms

- **without**: Claude Code on a clean checkout, no MCP servers.
- **with**: the same, plus AeroTrace installed the way a user would install it (MCP server, and the
  Claude Code hook if `aerotrace install` writes one by then; which one was used is recorded).
- **advice (optional)**: like "without", plus the same advice as plain text, with no tool (text below).
  This arm separates "the tool's data helped" from "being told to check callers helped". It runs only if
  time and budget allow, **after** the main batch. If it runs, it is reported separately, with the note
  that it ran at a different time and load from the "without" runs it is compared to.

Main batch: 20 tasks x 2 arms x 3 runs = **120 runs**, as 60 pairs. 3 runs per side is the minimum and is
not reduced.

### What the "with" side is told

The MCP server sends instructions that Claude Code puts into the system prompt. That is a nudge the
"without" side does not get. We count it as part of the product, and the report quotes it word for word,
taken from the run's own log. At 01032b7 it is (`src/aerotrace/mcp/server.py:26`):

```text
AeroTrace knows how this C/C++ codebase fits together: who calls what, through direct calls, virtual calls
and function pointers, and which globals and struct fields each function reads and writes. It runs locally
and its answers are deterministic.

Use it BEFORE you edit C or C++ code:
- Before changing a function, call `impact` on it. Read the affected callers and the "Limits of this answer".
- After editing, or before applying a patch, call `diff_impact` to check the whole change: changed and
  deleted functions, changed globals and fields, and everything upstream of them.
- Before changing a function's parameters or return type, call `signature_impact`: it lists every call
  site and how each one uses the return value.
- Use `callers` / `callees` to follow the code, and `find` when you are not sure of a name.
- Labels: no label = a direct call; [virtual] = may land on this override; [via_pointer] = matched by
  function-pointer type; [ambiguous] = several functions share this name; [data] = reads data the changed
  function writes; [uses_changed] = uses a global or field whose declaration changed; [calls_removed] =
  still calls a function the change removes. Treat labelled links as real possibilities, not proof.
- If a name matches several functions, ask again with `name@path/to/file.c` or `file.c:LINE`.
```

The hook's text (if there is a hook) is quoted the same way.

The advice arm gets this text through `--append-system-prompt` (the same place the MCP instructions go):

```text
Before you edit C or C++ code:
- Before changing a function, find everything that calls it, directly and through function pointers, and
  check each caller.
- After editing, check the whole change: changed and deleted functions, changed globals and struct fields,
  and everything that uses them.
- Before changing a function's parameters or return type, find every call site and how each one uses the
  return value.
- Treat possible links (calls through function pointers, several functions with the same name) as real
  possibilities, not proof.
```

### Model and settings (all arms)

One run, from the checkout's root:

```bash
CLAUDE_CONFIG_DIR="$RUN_TMP/claude-home" ~/.local/bin/claude -p "$PROMPT" \
  --model global.anthropic.claude-opus-5-5 --effort high \
  --output-format stream-json --verbose \
  --dangerously-skip-permissions \
  --setting-sources project \
  --strict-mcp-config $MCP_ARGS \
  --no-session-persistence \
  --max-budget-usd 20
```

- **The model id is pinned in full** (`global.anthropic.claude-opus-5-5`, the Bedrock id seen in review),
  not an alias. The harness also sets `ANTHROPIC_SMALL_FAST_MODEL` and the `ANTHROPIC_DEFAULT_*_MODEL`
  variables to the values recorded at batch start, so sub-agents use the same models in every run. For each
  run the harness records the model ids from the `system`/`init` event and from `modelUsage`, and the names
  (never the secrets) of `CLAUDE_CODE_USE_BEDROCK`, `AWS_REGION`, `AWS_PROFILE`, `ANTHROPIC_MODEL`,
  `ANTHROPIC_SMALL_FAST_MODEL` and `ANTHROPIC_DEFAULT_*_MODEL`. All runs must show the same values, or the
  affected runs are invalid (see "Invalid runs").
- **A fresh config dir for every run.** Claude Code writes into its config dir even with
  `--no-session-persistence` (review found `.claude.json` counters, `cache/`, `backups/`, `projects/` and
  plugin files after one trivial run). So the harness keeps one **seed** config dir and copies it to
  `$RUN_TMP/claude-home` for every run. The seed has no `CLAUDE.md`, plugins, skills, hooks, memory or MCP
  servers, and its file list and hashes are recorded. The copy is thrown away after the run, and its final
  contents are saved with the logs.
- `--setting-sources project` loads only the checkout's own `.claude/settings.json`, which exists only on
  the "with" side and only if `aerotrace install` writes one.
- `$PROMPT` is the task's prompt plus its repo suffix, exactly as in tasks.md, passed as one argument.
- `$MCP_ARGS`: empty for "without" and "advice" (so, with `--strict-mcp-config`, no MCP servers);
  `--mcp-config <checkout>/.mcp.json` for "with". The file is written by `aerotrace install --client claude`;
  passing it this way skips the approval prompt, which `claude -p` can't answer.
- Same Claude Code version for every run (recorded).
- Network: Claude Code needs it to reach the model. AeroTrace itself makes no network calls.

### Credentials

Model access depends on AWS credentials that expire (on the order of hours). Before every wave the harness:

1. checks how long the credentials have left (for SSO: `aws configure export-credentials --profile
   $AWS_PROFILE` gives `Expiration`). If less than 75 minutes are left (longer than one wave can take), it
   **stops cleanly**: it starts no new wave, lets running pairs finish, and prints how to resume
   (`bench/run.py run --resume`) after the operator refreshes the login. The harness never refreshes
   credentials by itself.
2. runs a trivial `claude -p "Reply with exactly: OK"` with the same flags in a throwaway folder (about
   $0.08). If that fails, it stops cleanly.

A run that ends with an authentication or credential error (expired token, 403 from the provider, as seen in
the `result` event or the stream) is a **harness failure**, not a counted failure. Its pair is rerun after
the login is refreshed, and both attempts are kept in the logs.

### Slots, ports and parallel pairs

To finish in a night, runs go in **matched pairs**: (task, run k, with) and (task, run k, without) start at
the **same time**, so both sides see the same machine load. **3 pairs (6 agents) run at once.** Pairs are
taken in the order of `sha256("<task>/<k>")`.

Each of the 6 agent slots has:

- its own temp folder `$RUN_TMP` (`<bench root>/runs/<task>-<arm>-<k>/`), holding the checkout, the
  config dir copy and all logs. Nothing goes to shared paths like `/tmp/agent.def`;
- `BENCH_JOBS=2` (the prompt tells the agent `make -j$BENCH_JOBS` and `./runtest --clients $BENCH_JOBS`);
- `BENCH_BASEPORT = 22000 + 1000 * slot`, with `--portcount 500` for `runtest`, and `BENCH_PORT =
  BENCH_BASEPORT + 600` for a server the agent starts by hand. Slot ranges never overlap, and none touches
  6379;
- its own process group. When the run ends (or times out), the harness kills the whole group, then any
  `redis-server` whose working folder or command line is inside `$RUN_TMP`. It checks that the slot's
  ports are free before the slot is used again.

The prompt text is identical in every run; only the environment variables differ between slots.

Agent wall time on a loaded machine is longer than on a quiet one. Absolute times are therefore not
comparable to other setups, and the report says so. The with/without comparison stays fair because each
pair shares the load.

### Setting up one run (clean checkout)

1. Unpack the pinned archive into the run's new temp folder with `tests/realrepo/repos.py` (`unpack()`
   checks the SHA-256 and writes the same `compile_commands.json` the golden tests use). Never reuse a
   folder.
2. Redis only: `make -C src -j2 MALLOC=libc` once, so the agent starts with built `deps/` and objects like a
   developer's working copy. Not counted in the agent's time.
3. `git init`, then `git add -A && git commit -m baseline`. Redis's own `.gitignore` keeps build output out;
   for SQLite the harness adds `sqlite3` and `*.o` to `.git/info/exclude`. `.aerotrace/`, `.mcp.json`
   and `.claude/` go into `.git/info/exclude` on every side.
4. "with" side only: `aerotrace index` (time recorded separately as "index time", not part of the agent's
   time); `aerotrace install --client claude`; `aerotrace status` must say the index is up to date, or the
   run is not started.
5. Copy the seed config dir, run the credential check (once per wave), then start the agent command.
   Wall-clock timeout: **45 minutes**. On timeout the process group is killed and the tree is saved as it
   is.
6. Save `git diff --binary baseline` (the agent's whole change; excluded paths are not in it), the stream
   log, the final config dir, and the run metadata (below).

Judging happens later, separately (next section). If the night ends before every pair has finished,
unfinished pairs are reported as **"not run"**. One side of a pair is never reported without the other.

### Judging a run

The judge **never uses the agent's tree**. Judging runs in its own queue of **2 slots** (judge ports
`30000 + 1000 * slot`), after or alongside the agent waves. The judge is deterministic, so it does not
need to share the agents' timing.

0. **Fresh copy.** Unpack the pinned archive into a new folder and apply the saved diff with
   `git apply --binary`. If it doesn't apply, the run fails ("diff does not apply").
1. **Diff gate.** The run fails if the diff
   - touches `tests/`, `runtest*`, `deps/`, `utils/`, `src/cli_commands.c`, `src/cli_commands.h`, or any
     `Makefile` (one exception: in task R15, `src/Makefile` may change only on the `REDIS_SERVER_OBJ=`
     line, and only by adding `object_reply.o`);
   - adds a line containing `-w ` or `-w"` as a compiler flag, `-Wno-`, `#pragma` together with
     `diagnostic`, or `#pragma GCC system_header`;
   - or touches anything the task's prompt says not to change.
   The judge's own scripts are in `$JUDGE` (`bench/judge/` in this repo, copied outside every checkout),
   so the agent can't see or change them.
2. **Build.** Redis: `make -C src -j2 MALLOC=libc > build.log 2>&1` from scratch. SQLite:
   `cc -O1 -Wall -o sqlite3 shell.c sqlite3.c > build.log 2>&1`.
   - `build_ok` = exit code 0.
   - `warnings` = number of lines in `build.log` containing `warning:` (unchanged sources: 0).
   - `build_clean` = `build_ok` and `warnings == 0`.
3. **Tests** (only if `build_ok`). Redis: `./runtest $RT` plus the task's units, where `$RT` is
   `--clients 2 --baseport <judge port> --portcount 500 --timeout 1200`, output to `test.log`.
   - `tests_ok` = exit code 0.
   - `failing_tests` = number of lines in `test.log` matching `[err]` or `[exception]`. A crash or timeout
     of the test runner counts as failing and is noted.
   - SQLite: `./sqlite3 :memory: < smoke.sql > smoke.out 2>&1`. `tests_ok` = `smoke.out` is byte-identical
     to the output of the unchanged build made the same way on the same machine (plus the file smoke test
     where the task says so).
4. **Checks.** The task's check commands, each `pass` or `fail`, with output saved. Checks that rebuild
   (R02 check 2) run last. Probes start their own server on the judge slot's port and always shut it down
   (see the probe scripts).
5. **Diff size.** Lines added and removed, files touched (reported, not used for "solved").

**Solved** = diff gate passes and `build_clean` and `tests_ok` and every check passes. Nothing else counts:
no partial credit and no human judgement.

### Flaky tests

Before the batch, each task's test line is run **3 times on the unchanged tree, under judge conditions**
(2 judge slots busy at once). Every test that fails at least once is put on that task's flaky list, and
the list is committed. When judging, if **every** failing test is on the flaky list, the test line is run
once more and the second result counts. Both results are recorded. The flake rate per task is in the
report.

### What is measured, per run

| Measure | Where it comes from |
|---|---|
| build breaks | `build_ok == false` (and separately: `warnings > 0`) |
| failing tests | `failing_tests` count, `tests_ok` |
| solved | as defined above |
| time | wall clock of the `claude` process (harness timer); also `duration_ms` and `duration_api_ms` from the final `result` event |
| tokens | from the final `result` event, **each reported separately**: `usage.input_tokens`, `usage.output_tokens`, `usage.cache_creation_input_tokens`, `usage.cache_read_input_tokens`, and `modelUsage` per model (sub-agents included). No single "total" is the headline, because cache reads dominate it |
| cost | `total_cost_usd` from the `result` event |
| turns | `num_turns` from the `result` event |
| AeroTrace use | number of `mcp__aerotrace__*` tool calls in the log ("with" side); a "with" run that never calls AeroTrace still counts as "with" |
| index time | "with" side, setup step 4 |
| diff size | lines added and removed, files touched |
| load | the 1-minute load average, sampled every 30 s during the run: start, end, median and max |

`--output-format stream-json` writes the whole session (every tool call and result) plus the final
`result` event, which has the same fields as `--output-format json`. The whole stream is the raw log.

### Reporting

- One row per run (all of them): task, arm, run, solved, gate, build_ok, warnings, tests_ok,
  failing_tests, each check, time, each token type, cost, turns, AeroTrace calls, diff size, load, and a
  link to the raw log.
- Per task and arm: solved out of 3, median and range of time, of each token type and of cost.
- Totals per arm: solved out of 60, build breaks, runs with failing tests, median time, median of each token
  type, total cost. The blind-spot tasks (B01, B02), the control (C02) and the tasks where we expected
  AeroTrace not to help (tasks.md, "How the tasks were chosen") are also shown separately. Totals are given
  with and without them.
- With 3 runs per side the numbers are small. The report says so, gives counts rather than percentages
  for single tasks, and makes no claim of statistical significance.
- Every task where "with" did worse than "without" on solved count is listed by name, with a short,
  factual note from the logs.
- The MCP instructions (and hook text) are quoted as described above, and the model provider (Bedrock) and
  ids are stated.

### Invalid runs

Decided now, with numbers:

- **Overload:** a pair is invalid if the median of its sampled 1-minute load average is above **24**
  (3 per core on this 8-core machine). The expected load with 6 agents and 2 judges is about 8 to 16. An
  invalid pair is rerun once at the end of the batch, and both attempts are reported. A second overload
  is reported as such and counts as "not run".
- **Model or tool drift:** a run whose recorded model ids, Claude Code version or AeroTrace version differ
  from the batch's values is invalid, and its pair is rerun once at the end.
- **Harness failure:** credential or authentication errors (see "Credentials"), the pinned archive's hash
  not matching, the harness crashing, or a slot's ports already being taken. The pair is rerun once.
- An API error that ends a single run for any other reason (overloaded, rate limited, server error) is
  **not** a reason to rerun: that run counts as it ended.

### Budget and time

- About $3 to $10 per run, so 120 runs cost about $400 to $1200. Each run is capped at $20
  (`--max-budget-usd`). The batch is capped at **$2400**: the harness starts no new pair once the spent
  amount plus 2 x $20 would pass the cap.
- About 20 waves of about 25 minutes each, so **8 to 10 hours** for the main batch. Serial runs would
  take about 40 hours, which is why pairs run in parallel.

### Probes and judge scripts

All of these live in `$JUDGE` (`bench/judge/`). Their text is fixed here.

`probe-r09.sh PORT` (task R09), from the judged copy's root:

```bash
#!/bin/sh
P=$1
cli() { ./src/redis-cli -p "$P" "$@"; }
./src/redis-server --port "$P" --save '' --appendonly no --daemonize yes --pidfile "$RUN_TMP/probe.pid" \
  --dir "$RUN_TMP" || exit 2
trap 'cli SHUTDOWN NOSAVE >/dev/null 2>&1; [ -f "$RUN_TMP/probe.pid" ] && kill "$(cat "$RUN_TMP/probe.pid")" 2>/dev/null' EXIT
i=0; until cli PING >/dev/null 2>&1; do i=$((i+1)); [ $i -gt 50 ] && exit 2; sleep 0.1; done
[ "$(cli CONFIG GET maxmemory-samples | tail -1)" = 5 ] || exit 1      # default
[ "$(cli CONFIG SET maxmemory-samples 64)" = OK ] || exit 1
[ "$(cli CONFIG GET maxmemory-samples | tail -1)" = 64 ] || exit 1
cli CONFIG SET maxmemory-samples 65 | grep -q ERR || exit 1           # above the range
cli CONFIG SET maxmemory-samples 0 | grep -q ERR || exit 1            # below the range
[ "$(cli CONFIG GET maxmemory-samples | tail -1)" = 64 ] || exit 1
exit 0
```

`probe-r10.sh PORT`: start the server the same way, then `cli INFO server | grep -q '^arch_bits:64'`.

`probe-b01.sh PORT PRISTINE_ROOT`: start the judged server on `PORT` and the unchanged server (from
`PRISTINE_ROOT`, built once per batch) on `PORT+1`, the same way; then
`cli COMMAND DOCS get set lindex xadd zadd` on both must be byte-identical.

`r09_check.sh SRC` (task R09, static):

```bash
#!/bin/sh
S=$1; ok=0
[ "$(grep -cE '^[[:space:]]+long long maxmemory_samples;' "$S/server.h")" = 1 ] || { echo "FAIL field type"; ok=1; }
[ "$(grep -c 'createLongLongConfig("maxmemory-samples"' "$S/config.c")" = 1 ] || { echo "FAIL not createLongLongConfig"; ok=1; }
[ "$(grep -c 'createIntConfig("maxmemory-samples"' "$S/config.c")" = 0 ] || { echo "FAIL still createIntConfig"; ok=1; }
if grep -n 'maxmemory_samples' "$S"/*.c "$S"/*.h | grep -qE '\*\)[[:space:]]*&|\(int[[:space:]]*\*\)'; then echo "FAIL pointer cast on maxmemory_samples"; ok=1; fi
exit $ok
```

`r02_contract.sh REDIS_ROOT OUTDIR` and `r02_contract.c` (task R02). The judged `util.c` is compiled on its
own and linked with the contract test, so no other part of the agent's change is involved:

```bash
#!/bin/sh
R=$1; O=$2; mkdir -p "$O"
cc -std=gnu11 -O0 -DREDIS_STATIC= -I"$R/src" -I"$R/deps/fpconv" -I"$R/deps/fast_float" -I"$R/deps/hiredis" \
   -c "$R/src/util.c" -o "$O/util.o" || exit 2
cc -std=gnu11 -O0 -I"$R/src" -c "$JUDGE/r02_contract.c" -o "$O/contract.o" || exit 2
cc -o "$O/contract" "$O/contract.o" "$O/util.o" -Wl,-undefined,dynamic_lookup 2>"$O/link.log" || exit 2
"$O/contract"
```

```c
/* Judge-owned contract test for task R02. Linked against the judged src/util.c. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "util.h"

static int parse(const char *s, long long *v) { return string2ll(s, strlen(s), v); }

int main(void) {
    long long v = 0;
    assert(parse("123", &v) == 0 && v == 123);              /* C_OK */
    assert(parse("-9223372036854775808", &v) == 0 && v == (-9223372036854775807LL - 1));
    assert(parse("0", &v) == 0 && v == 0);
    assert(parse("x", &v) == -1);                           /* C_ERR */
    assert(parse("", &v) == -1);
    assert(parse("12a", &v) == -1);
    assert(parse("9223372036854775808", &v) == -1);         /* overflow */
    assert(parse("01", &v) == -1);                          /* leading zero */
    puts("R02 contract OK");
    return 0;
}
```

#### argcount.py

`python3 -I $JUDGE/argcount.py ROOT NAME N [GLOB...]`.

```python
"""argcount.py ROOT NAME N [FILE_GLOB...]: list every use NAME(...) whose argument count is not N.

Comments and string literals are blanked first (line numbers kept), so text like `name()` in a comment
is ignored. Definitions and prototypes are checked too: their parameter count must also be N.
Exit 0 when every use has N arguments, 1 otherwise. Default globs: **/*.c **/*.h
"""

import pathlib
import re
import sys

root, name, want = pathlib.Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
globs = sys.argv[4:] or ["**/*.c", "**/*.h"]
LEX = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'', re.S)


def blank(m: re.Match) -> str:
    return re.sub(r"[^\n]", " ", m.group(0))


bad = 0
for g in globs:
    for p in sorted(root.glob(g)):
        text = LEX.sub(blank, p.read_text(errors="replace"))
        for m in re.finditer(r"\b%s\s*\(" % re.escape(name), text):
            i, depth, args = m.end(), 1, 0
            while i < len(text) and depth:
                ch = text[i]
                if ch in "([{":
                    depth += 1
                elif ch in ")]}":
                    depth -= 1
                if depth == 1 and ch == ",":
                    args += 1
                i += 1
            inner = text[m.end() : i - 1].strip()
            n = 0 if inner in ("", "void") else args + 1
            if n != want:
                bad += 1
                line = text[: m.start()].count("\n") + 1
                print(f"{p.relative_to(root)}:{line}: {name} has {n} arguments, want {want}")
sys.exit(1 if bad else 0)
```

Checked on the unchanged sources: `argcount.py src acceptCommonHandler 3`, `notifyKeyspaceEvent 4`,
`rdbFunctionLoad 5` and (SQLite) `sqlite3ExprAlloc 4` all exit 0. `acceptCommonHandler 2` lists exactly the
5 places (definition, prototype, 3 calls including `tls.c:874`).

#### idents.py

`python3 -I $JUDGE/idents.py ROOT NAME [GLOB...]`: every use of a name outside comments and strings.

```python
"""idents.py ROOT NAME [FILE_GLOB...]: print every use of the identifier NAME outside comments and strings.

Exit 0 when there is none, 1 otherwise. Default globs: **/*.c **/*.h
"""

import pathlib
import re
import sys

root, name = pathlib.Path(sys.argv[1]), sys.argv[2]
globs = sys.argv[3:] or ["**/*.c", "**/*.h"]
LEX = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'', re.S)
found = 0
for g in globs:
    for p in sorted(root.glob(g)):
        text = LEX.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), p.read_text(errors="replace"))
        for m in re.finditer(r"\b%s\b" % re.escape(name), text):
            found += 1
            print(f"{p.relative_to(root)}:{text[: m.start()].count(chr(10)) + 1}")
sys.exit(1 if found else 0)
```

Grep-based checks in tasks.md use `grep -I` so that built binaries (which contain names in their debug
information) never match.

#### SQLite smoke test

`smoke.sql`, run as `./sqlite3 :memory: < smoke.sql > smoke.out 2>&1`. It touches the code the SQLite
tasks change: error reporting (`Parse.nErr`), COLLATE, CAST, foreign key actions, generated columns,
upsert, triggers, ALTER TABLE, JSON, window functions and CTEs. Some statements fail on purpose; the error
messages are part of the expected output. On the unchanged build the output is 76 lines, exit code 1,
SHA-256 `65eac69dd150ced69c32faffcdeb23fb249b0dd567287fd594881da9aff5e311` (Apple clang 21, for reference
only; the judge always compares against the unchanged build made on the same machine). Two runs give
identical output.

```sql
.headers on
.mode list
.nullvalue NULL
PRAGMA foreign_keys=ON;
CREATE TABLE p(id INTEGER PRIMARY KEY, name TEXT COLLATE NOCASE UNIQUE, score REAL DEFAULT 0);
CREATE TABLE c(id INTEGER PRIMARY KEY, pid INTEGER REFERENCES p(id) ON DELETE CASCADE ON UPDATE CASCADE,
               v TEXT, total AS (length(v) * 2) STORED);
CREATE TABLE d(id INTEGER PRIMARY KEY, pid INTEGER REFERENCES p(id) ON DELETE SET NULL ON UPDATE CASCADE, w TEXT);
INSERT INTO p(name, score) VALUES ('alpha', 1.5), ('Beta', 2.25), ('gamma', NULL);
INSERT INTO c(pid, v) VALUES (1, 'x'), (1, 'yy'), (2, 'zzz'), (3, NULL);
INSERT INTO d(pid, w) VALUES (1, 'd1'), (2, 'd2');
SELECT name FROM p WHERE name = 'ALPHA';
SELECT name, CAST(score AS INTEGER) AS si, typeof(score) FROM p ORDER BY name COLLATE NOCASE;
SELECT p.name, count(c.id), sum(c.total) FROM p LEFT JOIN c ON c.pid = p.id GROUP BY p.id ORDER BY p.id;
UPDATE p SET id = 10 WHERE id = 1;
SELECT pid, v FROM c ORDER BY id;
DELETE FROM p WHERE id = 2;
SELECT id, pid, w FROM d ORDER BY id;
SELECT count(*) FROM c;
INSERT INTO p(name) VALUES ('alpha') ON CONFLICT(name) DO UPDATE SET score = score + 100;
SELECT name, score FROM p ORDER BY id;
WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r WHERE n < 10)
  SELECT group_concat(n, ','), sum(n) FROM r;
SELECT x, sum(x) OVER (ORDER BY x ROWS BETWEEN 1 PRECEDING AND CURRENT ROW) AS s,
       rank() OVER (ORDER BY x % 3) AS rk
  FROM (SELECT 1 AS x UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4) ORDER BY x;
SELECT json_object('a', 1, 'b', json_array(1, 2, 'three')), json_extract('{"k":[5,6]}', '$.k[1]');
SELECT key, value FROM json_each('{"x":1,"y":[2,3]}') ORDER BY key;
SELECT CASE WHEN 1 THEN 'one' ELSE 'other' END, coalesce(NULL, NULL, 'c'), nullif(3, 3), iif(0, 'a', 'b');
SELECT 'abc' LIKE 'A%', 'abc' GLOB 'a*', 'abc' BETWEEN 'abb' AND 'abd', 5 IN (1, 5, 9);
SELECT printf('%5.2f|%-4s|%d', 3.14159, 'ab', 42), hex('AB'), quote('it''s'), upper('mixed');
CREATE TABLE t(a, b);
CREATE INDEX t_ab ON t(a, b);
INSERT INTO t SELECT value, value * value FROM generate_series(1, 50);
SELECT count(*), max(b), min(a) FROM t WHERE a BETWEEN 10 AND 20;
CREATE VIEW v AS SELECT a, b FROM t WHERE a % 10 = 0;
SELECT * FROM v ORDER BY a;
CREATE TRIGGER tr AFTER INSERT ON t BEGIN INSERT INTO d(pid, w) VALUES (NULL, 'trig ' || new.a); END;
INSERT INTO t VALUES (100, 1);
SELECT w FROM d WHERE w LIKE 'trig%';
ALTER TABLE t RENAME COLUMN b TO bb;
ALTER TABLE t ADD COLUMN cc TEXT DEFAULT 'dflt';
SELECT a, bb, cc FROM t WHERE a = 100;
SELECT sql FROM sqlite_schema WHERE name IN ('t', 'v', 'tr') ORDER BY name;
BEGIN; SAVEPOINT s1; DELETE FROM t; ROLLBACK TO s1; RELEASE s1; COMMIT;
SELECT count(*) FROM t;
PRAGMA integrity_check;
PRAGMA foreign_key_check;
SELECT * FROM nosuchtable;
SELECT 1 +;
SELECT nosuchfunc(1);
CREATE TABLE p(x);
INSERT INTO c(pid, v) VALUES (999, 'orphan');
SELECT 'still running';
EXPLAIN QUERY PLAN SELECT a FROM t WHERE a = 5;
```

`smoke_file.sh SQLITE3_BINARY WORKDIR` (task B02): a file database in rollback-journal mode, so the
directory sync path (`openDirectory`) runs. On the unchanged build it prints `ok` and `301|301`, exit 0.

```bash
#!/bin/sh
B=$1; D=$2/smoke-file; rm -rf "$D"; mkdir -p "$D"; cd "$D" || exit 2
"$B" f.db "PRAGMA journal_mode=DELETE; PRAGMA synchronous=FULL; CREATE TABLE a(x INTEGER PRIMARY KEY, y TEXT);" >/dev/null || exit 1
for i in 1 2 3; do
  "$B" f.db "BEGIN; INSERT INTO a(y) SELECT 'row' || value FROM generate_series(1, 100); COMMIT;" || exit 1
done
"$B" f.db "PRAGMA journal_mode=WAL; INSERT INTO a(y) VALUES ('wal'); PRAGMA wal_checkpoint(TRUNCATE); PRAGMA journal_mode=DELETE;" >/dev/null || exit 1
"$B" f.db "VACUUM; PRAGMA integrity_check; SELECT count(*), max(x) FROM a;"
```

## Part A: accuracy

Question kinds: **callers**, **callees**, **blast radius** (transitive callers), **data dependencies**
(who reads and who writes a global or struct field) and **test impact** (which tests run a function). Same
pinned Redis 8.10.3 and SQLite 3.54.1, same `compile_commands.json` as `tests/realrepo/repos.py`.
AeroTrace is asked only through its normal CLI (`--json`).

### Who builds the truth

If an AI agent builds the truth (likely, for cost reasons), the report says so plainly. The 20% second
check below is then done by the **human owner**, not by another agent.

### Ids

Every function is named by a **file-qualified id**, `path/to/file.c:name` (`static` functions share names
across files). For a function defined in a header, the id uses the header path. Fields are
`struct_name.field`, globals `file.c:name` for `static` globals and `name` otherwise.

"Callers" means **caller functions**: the set of distinct functions that contain at least one call.
Call sites (file:line) are written down too, so the report can say which sites were missed, but they are
not scored.

### Picking what to check (before looking at any tool output)

The sample is drawn from lists that do not come from AeroTrace, using a fixed seed, and the sample is
committed before AeroTrace is run on it.

- **Functions:** Redis is normally built with `-flto`, and `nm` on LTO objects reads bitcode, which is not
  what we want. So the list comes from a separate build **without LTO**: `make -C src MALLOC=libc
  OPTIMIZATION=-O0` (no `-flto`). Take `nm -defined-only` on each `REDIS_SERVER_OBJ` object and on
  `sqlite3.o` (built with `cc -O0 -c`), symbol types `T` and `t`, written as file-qualified ids. Group them
  by how often the bare name appears in the compiled sources (0 to 1, 2 to 5, 6 to 20, more than 20). From
  each group take the functions whose `sha256("aerotrace-bench-1/" + id)` sorts first: Redis 12 per group
  (48), SQLite 8 per group (32). Functions reached only through pointers stay in: they are the hard cases.
- **Data:** listed **mechanically, before hashing**: every field of each chosen struct, read from its
  definition (Redis: `struct redisServer`, `struct client`, `struct redisObject`; SQLite: `Parse`, `Vdbe`,
  `sqlite3`), and every global with a definition in the compiled files (from `nm`, types `D`, `d`, `B`,
  `b`, `S`, `s`, `C`). Then take by the same hash order: Redis 12 fields of `redisServer`, 6 of `client`, 6 of
  `redisObject`, 6 globals; SQLite 10 fields spread over the three structs (4, 3, 3) and 5 globals.
- **Blast radius:** from the function sample, the first 10 Redis and 5 SQLite functions (hash order) whose
  hand-built radius at depth 3 has at most 60 functions. Larger radii are too costly to check by hand. The
  report says how many sampled functions were skipped for that reason, and their ids.
- **Test impact:** 20 Redis functions from the function sample (hash order). SQLite has no test suite in the
  amalgamation, so it has no test-impact score.

### Building the truth by hand

Each answer is written from the source, with the proof line (`file:line` and the copied text), the same
way `tests/realrepo/README.md` asks. Never from AeroTrace's output.

- **Callers of F:** search the whole tree for the name (`grep -rnw`), then read every hit. A hit is a
  caller if the code is compiled in this build (check the `#if` blocks around it; use `clang -E` with the
  file's flags when unsure) and it is a call, or a macro that expands to a call. Address-taken uses (`&F`,
  `F` in a table, `F` passed as an argument) are written down separately as **pointer** truth, with the
  place the pointer is later called if it can be found. Code that is not compiled (for example `tls.c`
  without `USE_OPENSSL`) is written down as "not compiled" and is not part of the truth.
- **Callees of F:** read F's body (after preprocessing when it uses macros) and list every function it
  calls directly. Calls through a pointer are listed as **pointer** truth, with the pointer's name.
- **Blast radius of F:** the closure, up to depth 3, of hand-checked direct callers (each level checked as
  above). Never from a tool.
- **Data:** every function that reads or writes the field or global. Search for the name, keep only hits
  on the right struct type (field names repeat, for example `nErr` in 7 SQLite structs), expand macros,
  and mark each hit as read, write, or address taken (the address escapes, so the function counts as a
  possible writer).
- **Test impact of F:** measured. Build Redis with coverage: `-O0`, no LTO, `-fprofile-instr-generate
  -fcoverage-mapping`, and `LLVM_PROFILE_FILE=<dir>/<unit>-%p.profraw` so that every server the test suite
  starts writes its own profile. Run each test unit from `tests/test_helper.tcl`'s list on its own, and
  record which units execute F at least once.

A second check covers a random 20% of the truth entries (same hash ordering), against the source. Every
disagreement is resolved from the source and counted in the report.

### Scoring

For every sampled item and question kind, with T = the tool's answer and H = the hand truth:

- precision = |T ∩ H| / |T| (how many of the tool's answers are right),
- recall = |T ∩ H| / |H| (how many of the true answers the tool found).

Each is reported per question kind and repo **two ways**: in total over all items (sum of matches over sum
of sizes), and as the average over items (macro average). The number of items with recall 1.0 is also
reported. Edge cases, decided now:

- An item with empty H and empty T counts as correct and adds nothing to the totals (it is left out of the
  averages, and the count of such items is given). Empty H with non-empty T: every answer is a false alarm.
- **Capped answers:** the CLI caps lists (`--limit`). Questions are asked with a limit larger than any
  truth set, and the report gives, per item, the tool's `total` and `omitted` counts. If anything was
  still omitted, the item is scored on what was shown, and this is marked.
- Callers and callees are scored twice: **exact** (tool answers with no label, against non-pointer truth)
  and **all** (every answer, whatever its label, against all truth including pointer truth). A wrong
  answer labelled `ambiguous` or `via_pointer` is a false alarm in "all".
- Answers the tool marks as a blind spot (it says it could not see something) are not answers, but every
  blind spot is listed next to the misses it explains.
- Blast radius: compare the sets of functions, ignoring depth. Data: score readers and writers separately.
  "Address taken" in the truth matches a tool answer of write or possible write.
- **Test impact: recall only.** Coverage is dynamic and the tool is static. A function the tool links to a
  test because it is statically reachable may simply not run in that test, so precision against coverage
  would be misleading. We report recall, and list the tool's extra answers without scoring them.

**Every miss and every false alarm is listed** by id, with its proof line, in a results file next to the
scores. A miss caused by a known bug links its issue.

If AeroTrace has no answer for a question kind when the benchmark runs (for example no test-impact feature
yet), the report says "not supported" for that kind. It never reports zero or skips the row.

Sample sizes (48 + 32 functions, 30 + 15 data items, 15 radii, 20 test-impact items) are enough for a first
report, not for fine comparisons. The report says so.

## Harness design (not written yet)

`bench/` in this repo, not in the package (pending the owner's OK, see the top):

```text
bench/run.py           uv run bench/run.py <command>
bench/tasks.py         the 20 tasks as data: prompt, units, checks, allowed diff exceptions
bench/judge/           argcount.py, idents.py, r02_contract.c/.sh, r09_check.sh, probe-*.sh, smoke.sql, smoke_file.sh
bench/patches/<id>/    reference.patch, wrong-*.patch
bench/accuracy/        sample lists, truth files (JSON per item), scoring
bench/results/<date>/  raw logs, diffs, judge output, summary
```

Commands:

- `prepare`: checks the pinned archives, tool versions, credentials and seed config dir, builds the
  unchanged Redis and SQLite once (for pristine outputs and probes), and measures the flake rate.
- `validate`: for every task, runs the empty diff, the reference patch and every wrong patch through the
  judge, and fails unless the verdicts are fail, pass, fail.
- `run [--dry-run] [--resume] [--arm advice]`: runs pairs in waves, as described above. `--dry-run`
  prints every command it would run (setup, agent, cleanup) without starting an agent or spending money.
- `judge [RUN...]`: judges saved diffs on fresh copies, in 2 judge slots.
- `report`: builds the per-run table and the summaries from the saved logs and judge output. Rerunning it
  on the committed logs reproduces every Part B number.
- `accuracy sample|ask|score`: draws the Part A sample, asks AeroTrace through the CLI and stores the raw
  answers, then scores them against the committed truth files.

`prompt` text and checks come only from `bench/tasks.py`. `docs/benchmark/tasks.md` is checked against it
(same prompts, same commands) before the first run.
