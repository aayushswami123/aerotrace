# Benchmark protocol

This file fixes how the public benchmark is run, **before any run**, so nobody can tune it after seeing
results. It covers Part A (accuracy of AeroTrace's answers) and Part B (does AeroTrace help a coding agent
on real tasks). The Part B tasks are in [tasks.md](tasks.md).

Rules that apply to everything below:

- Every run is reported, including runs where AeroTrace made things worse, runs that crashed and runs
  that timed out. Nothing is rerun to get a better number. The only allowed rerun is a whole batch, for a
  reason listed under "Invalid runs", and the discarded batch is reported too.
- Raw logs are committed (`bench/results/<date>/`), next to the summary made from them.
- `aerotrace bench` must reproduce every number in the report from those logs, and Part A from scratch.
- If this protocol has to change after the first run, the change is a new protocol version with a dated
  note at the top; results under different versions are never mixed in one table.

## Machine and feasibility (checked 2026-11-08, before any run)

| Item | Value |
|---|---|
| Machine | Apple M1 Pro, 8 cores, macOS 26.7.2, arm64 |
| Compiler | Apple clang 21.0.0 (clang-2100.1.1.101) |
| Tcl | `/usr/bin/tclsh` 8.5.9 (the Redis test suite needs 8.5 or newer) |
| Claude Code | `~/.local/bin/claude`, version 2.1.319 |
| AeroTrace | 0.1.0.dev0 at `launch-candidate` 01032b7 (the version used for runs is recorded per run) |

Redis 8.10.3 (pinned tarball from `tests/realrepo/repos.py`, unpacked into a temp folder):

- `make -j8 MALLOC=libc` at the top level **exits 2**: in 8.10.3 the top-level Makefile also tries to build
  the bundled modules (redisbloom, redisearch, redisjson, redistimeseries), which fail here. `redis-server`
  itself was built fine before that. So the build command for the benchmark is
  **`make -C src -j8 MALLOC=libc`**, which builds the server, cli, benchmark, check-rdb/aof and the test
  modules in `tests/modules`. It exits 0 with **0 compiler warnings**.
- Time: clean build about 35 s; rebuild after `touch src/server.h` about 24 s (link-time optimisation
  dominates).
- Build with C unit tests: `make -C src -j8 MALLOC=libc REDIS_CFLAGS=-DREDIS_TEST` about 27 s;
  `./src/redis-server test util` exits 0.
- Test suite works: `./runtest --single unit/type/string` passed in 16 s. Every test unit named in
  tasks.md was run once on the unchanged build and **all passed** (`./runtest --clients 8`, 39 units,
  about 2.5 minutes of wall time in total). Slowest units: `unit/type/stream` 80 s, `unit/maxmemory` 61 s,
  `unit/introspection` 54 s, `unit/scripting` 50 s, `unit/dump` 44 s, `integration/rdb` 44 s.
  Module API units (`unit/moduleapi/*`) run directly with `./runtest --single unit/moduleapi/<name>`;
  `./runtest-moduleapi` always runs all 49 module units (151 s), so we don't use it.
- Memory leak checks in the suite use macOS `leaks` and passed.

SQLite 3.54.1 amalgamation:

- `cc -O1 -Wall -o sqlite3 shell.c sqlite3.c` exits 0 with 0 warnings in about 9 s.
- The extended compile used by task S01 (R-Tree, Geopoly, FTS3/5, session, STAT4, dbstat, `SQLITE_DEBUG`,
  `-O0 -c`) exits 0 with 0 warnings in about 3 s.
- The amalgamation has **no test suite** (SQLite's Tcl tests are not in it). We use the smoke script below
  instead, and say so in the report.

## Part B: agent outcomes

### What is compared

Two sides, identical in every way except AeroTrace:

- **without**: Claude Code on a clean checkout.
- **with**: the same, plus AeroTrace installed in that checkout the way a user would install it.

20 tasks x 2 sides x 3 runs = **120 runs**.

### Model and settings (both sides)

The command for one run, from the checkout's root:

```bash
CLAUDE_CONFIG_DIR="$BENCH_HOME" ~/.local/bin/claude -p "$PROMPT" \
  --model opus --effort high \
  --output-format stream-json --verbose \
  --dangerously-skip-permissions \
  --setting-sources project \
  --strict-mcp-config $MCP_ARGS \
  --no-session-persistence \
  --max-budget-usd 20
```

- `$PROMPT` is the task's prompt plus its repo suffix, exactly as in tasks.md, passed as one argument.
- `--model opus` is an alias; the exact model id it resolves to is read from the `system`/`init` event of
  every run's log. **All 120 runs must show the same model id**, or the batch is invalid. The id is
  printed in the report.
- `$BENCH_HOME` is a Claude Code config folder made for the benchmark: no user `CLAUDE.md`, no plugins, no
  skills, no hooks, no memory, no MCP servers. Login is done once into that folder (or `ANTHROPIC_API_KEY`
  is set); the same folder is used for all runs. The harness checks before each run that it holds nothing
  else. This keeps the owner's own setup (for example memory plugins) out of the runs.
- `--setting-sources project` loads only the checkout's own `.claude/settings.json`, which exists only on
  the "with" side and only if `aerotrace install` writes one (see below).
- Same Claude Code version for every run (recorded; a different version makes the batch invalid).
- Network: Claude Code needs it to reach the model. AeroTrace itself makes no network calls.

`$MCP_ARGS`:

- **without**: empty (so, with `--strict-mcp-config`, no MCP servers at all).
- **with**: `--mcp-config <checkout>/.mcp.json`, the file written by `aerotrace install --client claude`.
  Passing it this way skips the interactive "approve this server" prompt that `claude -p` can't answer.

### Setting up one run (clean checkout)

For every run, from nothing:

1. Unpack the pinned archive into a new temp folder with `tests/realrepo/repos.py` (`unpack()` checks the
   SHA-256 and writes the same `compile_commands.json` the golden tests use). Never reuse a folder.
2. Redis only: `make -C src -j8 MALLOC=libc` once, so both sides start with built `deps/` and objects,
   like a developer's working copy. Not counted in the agent's time.
3. `git init`, then `git add -A && git commit -m baseline` (Redis's own `.gitignore` keeps build output out;
   for SQLite the harness adds `sqlite3`, `*.o` to `.git/info/exclude`). This lets us diff the agent's
   change. `.aerotrace/`, `.mcp.json` and `.claude/` are added to `.git/info/exclude` on both sides.
4. **with** side only:
   - `aerotrace index` (time recorded separately as "index time", not part of the agent's time);
   - `aerotrace install --client claude` (writes `.mcp.json`; if by then it also installs a Claude Code
     hook into `.claude/settings.json`, that is part of the "with" side and is recorded in the report);
   - `aerotrace status` must say the index is up to date, or the run is not started.
5. Start the agent command above. Wall-clock timeout: **45 minutes**. On timeout the process is killed and
   the tree is judged as it is.

Run order: all 120 runs are put in one list sorted by `sha256("<task>/<side>/<run>")` and run one at a
time in that order, so the two sides are interleaved over the day and API slowdowns hit both alike. No two
runs at once (build and test timing would interfere).

### Judging a run

After the agent stops, the judge works on the final tree. The agent's session is over; nothing it says is
trusted, only what the commands show.

1. **Build** (Redis): `make -C src clean && make -C src -j8 MALLOC=libc > build.log 2>&1`.
   SQLite: `cc -O1 -Wall -o sqlite3 shell.c sqlite3.c > build.log 2>&1`.
   - `build_ok` = exit code 0.
   - `warnings` = number of lines in `build.log` containing `warning:` (unchanged sources: 0).
   - `build_clean` = `build_ok` and `warnings == 0`.
2. **Tests** (only if `build_ok`): Redis: the task's `./runtest` line, output to `test.log`.
   - `tests_ok` = exit code 0.
   - `failing_tests` = number of lines in `test.log` matching `[err]` or `[exception]`; a crash or timeout of
     the test runner counts as failing and is noted.
   - SQLite: `./sqlite3 :memory: < smoke.sql > smoke.out 2>&1`; `tests_ok` = `smoke.out` is byte-identical
     to the output of the unchanged build made the same way on the same machine.
3. **Checks**: the task's check commands, each `pass`/`fail`, with their output saved. Checks that rebuild
   (R02 check 1) run last.
4. **Diff**: `git diff --stat` and `git diff` against the baseline commit, saved. Lines changed and files
   touched are reported (not used for "solved").

**Solved** = `build_clean` and `tests_ok` and every check passes. Nothing else, no partial credit, no
human judgement. If a check turns out to be wrong (it fails a correct change or passes a wrong one), the
check is fixed in a new protocol version and **all** runs of that task are judged again with the fixed
check; the old and new verdicts are both reported.

### What is measured, per run

| Measure | Where it comes from |
|---|---|
| build breaks | `build_ok == false` (and separately: `warnings > 0`) |
| failing tests | `failing_tests` count, `tests_ok` |
| solved | as defined above |
| time | wall clock of the `claude` process (harness timer); also `duration_ms` and `duration_api_ms` from the final `result` event |
| tokens | final `result` event: `usage.input_tokens`, `usage.output_tokens`, `usage.cache_creation_input_tokens`, `usage.cache_read_input_tokens`, and `modelUsage` per model (sub-agents included); total = sum of the four |
| cost | `total_cost_usd` from the `result` event |
| turns | `num_turns` from the `result` event |
| AeroTrace use | number of `mcp__aerotrace__*` tool calls in the log ("with" side); a "with" run that never calls AeroTrace is still counted as "with" |
| index time | "with" side, step 4 above |
| diff size | lines added/removed, files touched |

`--output-format stream-json` writes the whole session (every tool call and result) plus the final
`result` event, which has the same fields as `--output-format json`. The whole stream is the raw log.

### Reporting

- One row per run (all 120): task, side, run, solved, build_ok, warnings, tests_ok, failing_tests, each
  check, time, tokens, cost, turns, AeroTrace calls, diff size, and a link to the raw log.
- Per task and side: solved count out of 3, median and range of time and tokens.
- Totals per side: solved out of 60, build breaks, runs with failing tests, median time, median tokens.
  Control tasks (C01 to C03) are also shown separately, and totals are given with and without them.
- With 3 runs per side the numbers are small; the report says so, gives counts rather than percentages
  for single tasks, and makes no claim of statistical significance.
- Every task where "with" did worse than "without" on solved count is listed by name with a short,
  factual note from the logs.

### Invalid runs

A batch (all 120 runs) is thrown away and rerun only if: the model id or Claude Code version changed
between runs; the machine ran something else heavy during runs; the harness itself failed (for example
the pinned archive's hash did not match). An API error that ends a single run is **not** a reason: that
run counts as it ended. The thrown-away batch and the reason are kept in the report.

### Probes used by tasks

`probe-r09.sh` (task R09), from the repo root after the build:

```bash
./src/redis-server --port 21999 --save '' --appendonly no --daemonize yes --pidfile /tmp/r09.pid
sleep 1
cli() { ./src/redis-cli -p 21999 "$@"; }
[ "$(cli CONFIG GET maxmemory-samples | tail -1)" = 5 ] || exit 1      # default
[ "$(cli CONFIG SET maxmemory-samples 64)" = OK ] || exit 1
[ "$(cli CONFIG GET maxmemory-samples | tail -1)" = 64 ] || exit 1
cli CONFIG SET maxmemory-samples 65 | grep -q ERR || exit 1           # above the range
cli CONFIG SET maxmemory-samples 0 | grep -q ERR || exit 1            # below the range
[ "$(cli CONFIG GET maxmemory-samples | tail -1)" = 64 ] || exit 1
cli SHUTDOWN NOSAVE >/dev/null 2>&1; exit 0
```

`probe-r10.sh` (task R10): start the server the same way, then
`./src/redis-cli -p 21999 INFO server | grep -q '^arch_bits:64'`, then `SHUTDOWN NOSAVE`.

### argcount.py

Used by several checks. `python3 -I argcount.py ROOT NAME N [GLOB...]`.

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
`rdbFunctionLoad 5` and (SQLite) `sqlite3ExprAlloc 4` all exit 0; `acceptCommonHandler 2` lists exactly the
5 places (definition, prototype, 3 calls including `tls.c:874`).

### SQLite smoke test

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

## Part A: accuracy

Question kinds: **callers**, **callees**, **blast radius** (transitive callers), **data dependencies**
(who reads and who writes a global or struct field) and **test impact** (which tests run a function). Same
pinned Redis 8.10.3 and SQLite 3.54.1, same `compile_commands.json` as `tests/realrepo/repos.py`.

### Picking what to check (before looking at any tool output)

The sample is drawn from lists that do not come from AeroTrace, with a fixed seed, and committed before
AeroTrace is run on it.

- **Functions**: every defined function symbol in the built object files (`nm` on the Redis
  `REDIS_SERVER_OBJ` objects and on `sqlite3.o`, types `T` and `t`). Grouped by how many times the name
  appears in the source as `name(` or as a bare name (0 to 1, 2 to 5, 6 to 20, more than 20 uses). From
  each group the functions whose `sha256("aerotrace-bench-1/" + name)` sort first are taken:
  Redis 12 per group (48), SQLite 8 per group (32). Functions that are only reached through pointers are
  not removed: they are the hard cases.
- **Data**: struct fields and globals that the code reads or writes. Redis: 12 fields of
  `struct redisServer`, 6 of `struct client`, 6 of `struct redisObject` or other structs, 6 globals.
  SQLite: 10 fields of `Parse`, `Vdbe`, `sqlite3` and 5 globals. Same hash ordering, inside each group.
- **Blast radius**: 10 Redis and 5 SQLite functions from the function sample whose hand-built radius
  (depth 3) has at most 60 functions (larger ones are too costly to check by hand; the report says this
  limit).
- **Test impact**: 20 Redis functions from the function sample. SQLite has no test suite in the
  amalgamation, so it has no test-impact score.

### Building the truth by hand

Each answer is written by a person from the source, with the proof line (`file:line` and the copied text),
the same way as `tests/realrepo/README.md` asks. Never from AeroTrace's output.

- **Callers of F**: search the whole tree for the name (`grep -rnw`), then read every hit. A hit is a
  caller if the code is compiled in this build (check the `#if` blocks around it; `clang -E` with the
  file's flags when unsure) and it is a call or a macro that expands to a call. Address-taken uses
  (`&F`, `F` in a table or passed as an argument) are written down separately as **pointer** truth, with
  the place the pointer is later called if it can be found. Code that is not compiled (for example
  `tls.c` without `USE_OPENSSL`) is written down as "not compiled" and is not part of the truth.
- **Callees of F**: read F's body (after preprocessing when it uses macros) and list every function it
  calls directly; calls through a pointer are listed as **pointer** truth with the pointer's name.
- **Blast radius of F**: the closure, up to depth 3, of hand-checked direct callers (each level checked as
  above). Not from any tool.
- **Data**: every function that reads or writes the field or global: search for the name, then keep only
  hits on the right struct type (field names repeat, for example `nErr` in 7 SQLite structs), expand
  macros, and mark each as read, write, or address taken (the address escapes, so the function counts as a
  possible writer).
- **Test impact of F**: measured, not guessed: build Redis with coverage (`-fprofile-instr-generate
  -fcoverage-mapping`), run each test unit from `tests/test_helper.tcl`'s list one at a time, and record
  which units execute F at least once. A unit is in the truth for F if F ran during it.

A second person checks a random 20% of the truth entries (same hash ordering) against the source. Every
disagreement is resolved from the source and counted in the report.

### Scoring

For every sampled item and question kind, with T = the tool's answer and H = the hand truth:

- precision = |T ∩ H| / |T| (how many of the tool's answers are right),
- recall = |T ∩ H| / |H| (how many of the true answers the tool found).

Reported per question kind and repo as the total over all items (sum of matches over sum of sizes), plus
the number of items with recall 1.0. Edge cases, decided now:

- An item with empty H and empty T counts as correct and adds nothing to the sums. Empty H with non-empty
  T: every answer is a false alarm.
- Callers and callees are scored twice: **exact** (tool answers labelled `exact` against non-pointer
  truth) and **all** (every answer, whatever its label, against all truth including pointer truth). An
  answer labelled `ambiguous` or `via_pointer` that is wrong is a false alarm in "all".
- Answers the tool marks as a blind spot (it says it could not see something) are not answers, but every
  blind spot is listed next to the misses it explains.
- Blast radius: compare the sets of functions, ignoring depth. Data: score readers and writers separately;
  "address taken" in the truth matches a tool answer of write or possible write.
- Test impact: the tool's answer is a set of test units (or files mapped to units).

**Every miss and every false alarm is listed** by name with its proof line, in a results file next to the
scores. A miss caused by a known bug links its issue.

If AeroTrace has no answer for a question kind when the benchmark runs (for example no test-impact
feature yet), the report says "not supported" for that kind; it does not report zero or skip the row.

## Reproducing

`aerotrace bench` will:

1. Check the pinned archives (SHA-256) and the tool versions above, and print what differs.
2. Part A: unpack, index, ask every sampled question, score against the committed truth, write the results
   and every miss and false alarm.
3. Part B: either run the whole batch (needs Claude Code and an account) or, with the committed raw logs,
   re-judge and recompute every number in the report.

Not decided here, for the owner: `aerotrace bench` builds and tests Redis and SQLite. That is the pinned
copies in a temp folder, never a user's tree, but CLAUDE.md rule 6 says AeroTrace never runs a build on
its own. The benchmark harness may fit better as a separate script (for example `bench/` run with `uv run`)
than as a subcommand of the product.
