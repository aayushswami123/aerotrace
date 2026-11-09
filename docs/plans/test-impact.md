# Plan: test impact (`aerotrace tests`)

Status: plan, for review. No product code yet.
Branch: `feat/test-impact` (from `origin/launch-candidate`).

## 1. Goal

Tell an agent which tests to run for a change, why, and how sure we are. Also show, clearly, the changed
code that no test reaches, and say "I don't know" when that is the truth.

Two sources of links:

1. **Static**: C/C++ test entry points found in the index (CTest, GoogleTest, Unity, CMocka, test files
   with `main`), linked to the changed functions by walking callers (`graph.walk_many`).
2. **Coverage**: `aerotrace tests record` runs the project's tests once, when the user asks, with gcov,
   and saves which test ran which function. This is the only way to see Redis's and SQLite's tests,
   which are Tcl scripts.

Never print "no tests affected". The answer is one of: these tests reach it; no recorded test reaches it
(with what was recorded); or not known.

## 2. What I found (experiments in /tmp, nothing in the repo)

Machine: macOS, 8 cores.

- `/usr/bin/gcov` is **Apple LLVM's gcov** (`gcov --version`: "Apple LLVM version 21.0.0"). `/usr/bin/gcc`
  is Apple clang. `llvm-cov` is in fact installed (`xcrun -f llvm-cov` finds it), but we don't need it.
  CI's Ubuntu runner has GNU gcc/gcov, so we must read **both** gcov flavours:
  - LLVM gcov: `gcov -i -o <file.gcda> <src.c>` writes `<src>.gcov` in the current folder, text lines
    `file:<path>` and `function:<start line>,<count>,<name>`. (`-i -t` prints nothing; it must write a file.)
  - GNU gcov (gcc 9+): `gcov --json-format --stdout`, functions with `start_line`, `end_line`,
    `execution_count`, `name`, `demangled_name`. Not testable on this Mac; CI (Ubuntu) covers it.
- **`GCOV_PREFIX` works** with clang's runtime (and GCC's): `GCOV_PREFIX=/tmp/x ./runtest ...` wrote all
  120 `.gcda` files under `/tmp/x/private/tmp/.../src/` and **none** in the source tree. So each test gets
  its own fresh counters, no reset step, no `.gcda` left in the user's tree, and tests can run in
  parallel. To read them, we symlink the matching `.gcno` next to each `.gcda` in our temp folder and run
  gcov from a temp folder (gcov writes its output into the current folder).
- **Redis 8.10.3** has a built-in target: `make -C src gcov` (= `-fprofile-arcs -ftest-coverage
  -DCOVERAGE_TEST`). It built in **31 s** (`make -j8 gcov`). `COVERAGE_TEST` makes forked children call
  `exit()` instead of `_exit()` so they also save coverage (src/server.c:287).
  - Tests: 158 units (`./runtest --list-tests`), Tcl files under `tests/unit`, `tests/integration`, ...
    `./runtest --single <unit>` runs one; `--single` can be repeated (test_helper.tcl:566).
  - Timed with coverage, `--clients 8`: unit/keyspace 3 s, unit/type/hash 5 s, unit/type/incr 3 s,
    unit/type/list 14 s, unit/type/string 14 s, unit/expire 27 s. All passed.
  - gcov over one unit's 120 `.gcda`: **3 s**, 5,576 function records, 1,704 with count > 0.
  - Attribution works per unit: `psetexCommand` (src/t_string.c:456) ran 113 times in `unit/expire` and
    **0** times in `unit/type/string`. Source agrees: `tests/unit/expire.tcl:96` is
    `test "PSETEX can set sub-second expires" {`, and `string.tcl` never says "psetex".
  - Static links can't see this: `psetexCommand` is only reached through the command table.
  - Redis's C unit tests (`redisTests[]` in src/server.c:8015, `sdsTest`, `ziplistTest`, ...) sit inside
    `#ifdef REDIS_TEST` (server.c:7996), which the pinned index does not define. So static finds
    **no** test entry points in pinned Redis.
- **SQLite**: the pinned amalgamation has no tests. The full source,
  `https://www.sqlite.org/2026/sqlite-src-3540100.zip` (14.2 MiB), has 1,199 `test/*.test` Tcl files.
  - SHA3-256 `877c6f4996ce7adb368686bce21638de9e3d91534832ad3d9d119037d1bc22ad` matches sqlite.org's
    download page; SHA-256 computed: `387e7f6cad4797b17848c07d453dc6606917bb356a6cfe5a3abafd38c240c71b`.
  - Out-of-tree build works with the macOS SDK's Tcl 8.5:
    `CFLAGS="--coverage -O0 -g" LDFLAGS=--coverage ../configure --with-tcl=<SDK>/Tcl.framework` (7 s),
    `make -j8 testfixture` (13 s). Ubuntu will need `tcl-dev`.
  - `./testfixture test/select1.test`: 1 s, 199 tests, 0 errors. 2,774 functions in `sqlite3.c`, 1,348 ran.
    `sqlite3Select` (sqlite3.c:157288) ran 311 times; `sqlite3WindowCodeStep` (180412) and
    `jsonExtractFunc` (218016) ran 0 times (select1.test has no `OVER` or `json`).
  - `func.test` had 9 failures on macOS (exit 1). Failing tests still give coverage; we record exit codes.
  - The testfixture build compiles a **generated `sqlite3.c`, byte-identical (`cmp`) to the pinned
    amalgamation's `sqlite3.c`**. So coverage from the full-source build maps line for line onto the
    pinned amalgamation index. (Someone who indexes the full source tree instead would need
    `--linemacros` or `--disable-amalgamation` so coverage points at `src/*.c`; we say so when coverage
    files don't match any indexed file.)
- `cmake` is not installed here. CTest support is file parsing only (no cmake run), tested by fixtures.

## 3. Static detection rules

All of this reads the index plus source text (read-only). Every rule is tested by a fixture with a trap.

| Kind | Found by | Entry function | Test id | Run command |
|---|---|---|---|---|
| GoogleTest | indexed method `TestBody` of a class named `<Suite>_<Name>_Test`, and the source line at `line_start` starts with `TEST(`, `TEST_F(`, `TEST_P(` or `TYPED_TEST(` | that `TestBody` | `Suite.Name` | `<binary> --gtest_filter=Suite.Name:...` when the binary is known (CTest / `add_executable`), else listed with "binary unknown" |
| Unity | `RUN_TEST(fn)` / `RUN_TEST(fn, line)` in an indexed file (comments and `#if 0` blocks skipped by using the index: `fn` must be called from that file's function) | `fn` | `file:fn` | the runner executable if known |
| CMocka | `cmocka_unit_test(fn)`, `cmocka_unit_test_setup(fn, ...)`, `cmocka_unit_test_teardown`, `cmocka_unit_test_setup_teardown`, `cmocka_unit_test_prestate*` | `fn` | `file:fn` | the executable if known |
| Test file with `main` | `main` defined in a file named `test_*.c`, `*_test.c`, `*_tests.c`, `test*.c(c/pp)`, `*_unittest.cc`, or any file under a folder named `test`, `tests`, `unittest(s)` | that `main` | file path | the executable if known |
| CTest | `add_test(NAME n COMMAND tgt ...)`, `add_test(n tgt ...)`, `gtest_discover_tests(tgt)`, `gtest_add_tests(TARGET tgt)` in any `CMakeLists.txt` / `*.cmake`, joined to `add_executable(tgt srcs...)` | the entries above inside `srcs` | CTest name | `ctest --test-dir <build> -R '^(a|b)$'` |

Rules that keep it honest:

- A Unity/CMocka function that is defined but not registered is **not** a test (it never runs). Trap
  fixture: `test_unused()` defined, `RUN_TEST` for it commented out.
- `test_helper()` in a non-test file is not an entry point.
- CMake with variables we can't expand (`${EXE}`, generator expressions) or `add_test` running a script:
  listed as an **unknown** test ("runs <cmd>; AeroTrace can't see what it runs"), counted, capped.
- Test source files found on disk but not in the index (not in `compile_commands.json`): counted in the
  limits ("3 test files are not indexed: ...; their links are not known").
- Link: callers walk from the changed functions (`graph.walk_many`, `direction="callers"`, depth default
  8, same confidence order). Any hit (or start) that is an entry point gives a test. For a diff, the seeds
  are the diff's own seeds (changed functions, plus readers of changed globals/fields via `diff._seeds`),
  so data links count too. If the walk is cut at the depth limit, we say so.

## 4. Coverage recording

### Command

```
aerotrace tests record [--config FILE] [--only ID ...] [--jobs N] [--dry-run] [--yes]
```

- Needs a config; it never guesses what to run. With no config it prints what it found and an example
  config (for example a Redis-shaped one when `runtest` and `src/Makefile` with a `gcov:` target exist),
  then exits 2 without running anything.
- Before running, it prints **every** command it will run, the number of tests, and where coverage goes,
  then asks `Run these? [y/N]`. With stdin not a terminal, it refuses unless `--yes` is given.
  `--dry-run` prints the plan and stops.
- It does not build by default. It checks that `.gcno` files exist under the configured object folders;
  if not, it stops: "No .gcno files under src/. Build with coverage first, for example: make -C src gcov".
  A config may name a `build` command; it is then shown in the plan and run first, only after the user
  says yes.

### Config (`.aerotrace/tests.toml`, read with stdlib `tomllib`)

```toml
version = 1
# build = "make -C src gcov"         # optional; shown and confirmed like everything else
objects = ["src"]                     # folders holding .gcno files (relative to the repo)
list = "./runtest --list-tests"       # prints one test id per line (or use: tests = ["unit/expire", ...])
run = "./runtest --single {test} --clients 4 --baseport {port}"
run_many = "./runtest {tests}"        # optional, for the ready-to-run command
each = "--single {test}"              # how one test is written inside {tests}
timeout = 1800                        # seconds per test
```

`{port}` gives each parallel job its own port range (base 21111 + job * 100) so Redis units can run
side by side. With `--jobs 1` (the default) it is always 21111.

### Per-test attribution

Chosen: **run each test id as its own process tree, with its own `GCOV_PREFIX` folder** (in a temp
folder outside the repo). One Redis unit starts several servers; they all write into that unit's folder
and their counters add up. This gives per-unit attribution with no counter reset, no `.gcda` in the
user's tree, and safe parallel runs.

Rejected:
- Deleting `.gcda` files between tests: serial only, writes in the user's build tree, races with forks.
- Calling `__gcov_reset` / `__gcov_dump` inside the server between test cases: needs changes to the
  project's code.

So the unit of attribution is what the config calls a test: a Redis unit file, a SQLite `.test` file, a
CTest test, or one GoogleTest case if the user lists cases. Finer than that is not possible here, and
the output says so.

After each test: for each `.gcda`, symlink its `.gcno`, run gcov in a temp folder, parse, keep
`(source file, function name, start line, count > 0)`. gcov flavour comes from `gcov --version`; a
`--gcov PATH` option (and `gcov =` in config) picks another one. A gcov/compiler mismatch error is shown
as is, with a hint.

Interrupted (Ctrl-C) or a test times out: what finished is saved and the file is marked `partial`.

## 5. File format: `.aerotrace/tests.json` (separate file; index unchanged, SCHEMA_VERSION stays 3)

```json
{
  "format": "aerotrace-tests",
  "version": 1,
  "aerotrace": "0.4.0",
  "complete": true,
  "recorded": {
    "commit": "<git HEAD sha or null>",
    "dirty": ["src/t_string.c"],
    "gcov": "Apple LLVM version 21.0.0",
    "config": { "...": "the config used, copied in" }
  },
  "tests": [
    {"id": "unit/expire", "run": "./runtest --single unit/expire --clients 4 --baseport 21111", "exit": 0}
  ],
  "files": {
    "src/t_string.c": {
      "sha256": "<content at record time>",
      "functions": [["psetexCommand", 456, [0, 3]], ["msetexCommand", 821, []]]
    }
  }
}
```

- `tests` sorted by id; `files` by path; functions by line; test lists are sorted indexes into `tests`.
- Every instrumented function is listed, even with `[]`. That is how "instrumented, no test ran it"
  differs from "not built with coverage".
- No timestamps or durations (rule 1). Paths are repo-relative; coverage for files outside the repo
  (system headers) is dropped. A generated file (SQLite's build-folder `sqlite3.c`) is matched to an
  indexed file only when the content hash is equal; otherwise it is counted as "coverage for files
  AeroTrace has not indexed".
- Written atomically (temp file + rename). Size estimate for Redis: 158 tests x ~5,600 functions, a few MB.
- A different `version` is never read: "Test data in .aerotrace/tests.json is from another AeroTrace
  version; run `aerotrace tests record` again."

Matching a covered function to an index function: same file, and the gcov start line falls inside the
function's `[line_start, line_end]`, and the name matches (`qualified_name`, or for C++ the demangled
name without parameters). gcov's line is the name line; libclang's extent may start a line earlier
(`static int\nfoo(...)`), which the range check covers.

## 6. Staleness rules

Checked per changed file, not per run:

1. No `tests.json`: coverage "not recorded". Static only. Nothing is ever "no test reaches" from coverage.
2. Other format version, or damaged: treated as not recorded, with the reason.
3. The changed file's hash at record time equals its content on the side the index describes before the
   change (git mode: the base version, read with `git show`; patch mode: the file on disk) **or** its
   current content: **fresh** for that file.
4. Otherwise **outdated** for that file: "src/t_string.c changed since coverage was recorded (at
   abc1234)". Tests found are `probable`; "no test reaches" becomes `unknown`.
5. Changed file with no coverage entry (not compiled with coverage, or not in any test binary): `unknown`.
6. New function (not in the recorded file): `unknown`, "new; no coverage yet".
7. `complete: false`, or some tests failed to start: said once at the top, and "no test reaches" claims
   become `probable` ("only 120 of 158 tests were recorded").
8. Recorded commit differs from the base commit: one note line (the hash check above decides).
9. A test exited non-zero: still used, marked "(failed when recorded)".

## 7. Certainty rules

For a test (should I run it?):

- **exact**: fresh coverage shows the test ran a changed function; or a static path from the test entry
  to a changed function uses only `exact` calls.
- **probable**: coverage of an outdated file version; a static path with a `virtual`, `ambiguous`,
  `via_pointer` or `data` link (reason from `certainty.of_call`); a CTest/GoogleTest case whose binary is
  built from a file that contains a changed function but no call path was found (shares a binary).
- **unknown**: a test AeroTrace can't see into (CTest running a script, unexpanded CMake). Listed only
  when the changed code is otherwise not covered, capped.

For changed code:

- **No test reaches it** (shown first, prominent), only when: fresh coverage, the function was
  instrumented, count 0 in every recorded test, and no static test link. Level `exact` if the record is
  complete, `probable` if partial. Static only: `probable`, and only when the repo has at least one
  static test entry and every test file found is indexed; the reason says pointer calls and scripts are
  not seen.
- **Not known** (also shown before the test list): everything else without a test (no coverage, outdated,
  new, file not instrumented).

Sorting: tests by level (exact, probable, unknown), then by how many changed functions they reach (most
first), then id. Changed code by file, then line. Lists are capped by `--limit` with "and N more".

## 8. Output, word for word

Redis, coverage recorded for all 158 units at the base commit, change to two functions in t_string.c
(the counts and test names below are illustrative; the wording and layout are the spec):

```
Tests for this change, compared with HEAD (abc1234): 2 functions changed in 1 C/C++ file.
Coverage: 158 tests recorded at abc1234 (.aerotrace/tests.json); fresh for every changed file.

Changed code no test reaches (1):
  - msetexCommand  src/t_string.c:821-850  (exact: built with coverage, none of the 158 recorded tests ran it)

Run these tests (4, most certain first):
  - exact  unit/expire     runs psetexCommand (coverage)
  - exact  unit/other      runs psetexCommand (coverage)
  - exact  unit/pubsub     runs psetexCommand (coverage)
  - exact  unit/scripting  runs psetexCommand (coverage)

Ready to run:
  ./runtest --single unit/expire --single unit/other --single unit/pubsub --single unit/scripting

Limits of this answer:
  - Coverage is per test file, not per test case.
  - Code that ran in a process killed with SIGKILL or ended by _exit() or abort() is not counted.
  - Static links: no C/C++ test entry points in the index (looked for CTest, GoogleTest, Unity, CMocka,
    test files with main). Tests outside the index (Tcl, Python, shell) are only known through coverage.
```

The test names come from grepping `psetex` in tests/ (expire, other, pubsub, scripting); they are not yet
checked by a full record. Line format: `  - <level, padded>  <test id, padded>  runs <changed functions,
up to 3, then "and N more"> (<coverage | calls it: a -> b -> c | via pointer: ... | failed when recorded>)`.
A probable line looks like: `  - probable  tests/test_queue.c  runs queue_push (calls it through a function
pointer: run_case -> queue_push)`.

No coverage recorded, same change (this one is exact text, nothing illustrative):

```
Tests for this change, compared with HEAD (abc1234): 2 functions changed in 1 C/C++ file.
Coverage: not recorded. Run `aerotrace tests record` to learn which tests run this code.

Not known which tests reach this code (2):
  - msetexCommand  src/t_string.c:821-850  (no coverage data; no C/C++ test calls it)
  - psetexCommand  src/t_string.c:456-458  (no coverage data; no C/C++ test calls it)

No tests to list. This does not mean no test runs this code: AeroTrace found no C/C++ test entry points,
and tests written as scripts (Tcl, Python, shell) are only visible through `aerotrace tests record`.

Limits of this answer:
  - Static links: no C/C++ test entry points in the index (looked for CTest, GoogleTest, Unity, CMocka,
    test files with main).
```

(Before shipping I will replace the illustrative block with the real output on pinned Redis and keep
both examples in `tests/golden/` as expected text for the fixture runs.)

`--json`: `{"changed": [...], "no_test_reaches": [...], "not_known": [...], "tests": [{"id", "level",
"reason", "reaches": [...], "source": "coverage|static"}], "commands": [...], "coverage": {"state":
"fresh|outdated|partial|not_recorded", ...}, "limits": [...], "omitted": {...}}`, sorted, capped.

## 9. CLI and MCP

- `aerotrace tests [BASE] [--patch FILE] [--symbol FN] [--build-dir D] [--depth N] [--limit N] [--json]`
  - Same change loading as `diff` (git mode / patch mode), or one function with `--symbol`.
  - Never runs anything.
- `aerotrace tests record ...` (section 4).
- `aerotrace tests status`: what `tests.json` holds and which recorded files changed since.
- A branch literally named `record` or `status`: `aerotrace tests --base record`. (`BASE` is also
  accepted as `--base`.)
- MCP: one read-only tool, `tests_for_change(base="HEAD", patch="", symbol="", depth=8, limit=30)`.
  **No record tool over MCP**: recording runs the user's tests, so only the user starts it.
- New code: `src/aerotrace/analysis/tests.py` (detection, linking, staleness, certainty),
  `src/aerotrace/analysis/coverage.py` (gcov parsing, tests.json read/write),
  `src/aerotrace/toolchain/testrun.py` (the runner; the only place that starts processes),
  `render.tests_text/tests_json`. Shared files get small, additive changes: one subparser block and
  `cmd_tests` in cli.py, one tool in mcp/server.py, two functions appended to render.py.
- **Diff output not changed.** Proposal for the owner: one extra line at the end of `aerotrace diff`,
  "Tests: run `aerotrace tests` (coverage recorded at abc1234)" or "(no coverage recorded)".

## 10. Fixture tests (`tests/fixtures/testimpact/`, written to trip the code up)

- `gtest/`: tiny stub `gtest/gtest.h` whose `TEST` macro expands like the real one (class
  `Suite_Name_Test`, `TestBody`). Traps: a `TEST` inside `#if 0`; a function reached only via a pointer
  (probable); a function no test reaches; a test file missing from `compile_commands.json` (counted);
  two `TestBody` in different suites calling the same helper.
- `cmake/`: `CMakeLists.txt` with `add_test(NAME ...)`, old-style `add_test(n tgt)`, `gtest_discover_tests`,
  `add_test` with `${EXE}` (unknown, counted), `add_test` running a `.sh` (unknown).
- `unity/`: runner with `RUN_TEST(test_add)`, a commented-out `RUN_TEST(test_unused)` (must not be a
  test), `test_helper()` in a non-test file (must not be a test).
- `cmocka/`: array of `cmocka_unit_test(...)` and `cmocka_unit_test_setup_teardown(...)`.
- `plain/`: `tests/test_queue.c` with `main` calling `queue_push`; `src/queue.c` with an untested
  `queue_drain`.
- Coverage (`needs_gcov` marker: skipped with a printed reason when `cc` or `gcov` is missing; CI has both):
  build a 3-function program with `cc --coverage` in a temp folder, two tests (`./prog a`, `./prog b`)
  - attribution: `fa` only in test a, `fb` only in test b, `fc` in none -> "no test reaches".
  - edit the file after recording -> outdated -> `probable` / `unknown`, never "no test reaches".
  - add a function -> `unknown`.
  - a test that calls `abort()` -> its coverage is missing; the limits line is there.
  - `--only a` -> partial -> "no test reaches" drops to `probable`.
  - after `record`, **no `.gcda` file exists in the source folder** (rule 6).
  - non-terminal stdin without `--yes` -> refuses, runs nothing.
  - tests.json with another version -> "not recorded" with the reason.
- gcov parsers: small sample outputs of LLVM `-i` text and GNU `--json-format` (gcov's output, not ours),
  including C++ mangled names and a header function seen from two files.
- Determinism: two records of the same suite give byte-identical `tests.json`.
- Never the phrase "no tests affected" in any output (grep in every test's output).

## 11. Golden facts on Redis and SQLite

In the normal realrepo suite (no build, static only):

- Redis, change `psetexCommand`: must say coverage not recorded and list `psetexCommand` under
  "Not known". Proof: src/t_string.c:456 `void psetexCommand(client *c) {`; it is only named in
  `commands.def`, never called directly. **Must not**: list it under "no test reaches"; print "no tests".
- Redis: **must not** report `sdsTest`, `ziplistTest` or any `redisTests[]` entry as a test entry point:
  they are inside `#ifdef REDIS_TEST` (src/server.c:7996), which the pinned flags don't define.
- SQLite amalgamation, change `sqlite3Select`: no test entry points, "Not known". **Must not**: "no test
  reaches".

Coverage facts (new opt-in marker `realrepo_coverage`; they run the repos' builds, see section 13):

- Redis: build with `make -C src gcov`, record `unit/expire` and `unit/type/string`.
  - `unit/expire` runs `psetexCommand`. Proof: tests/unit/expire.tcl:96
    `test "PSETEX can set sub-second expires" {` and src/t_string.c:456.
  - **must not**: `unit/type/string` runs `psetexCommand`. Proof: no line in tests/unit/type/string.tcl
    contains "psetex" (case-insensitive grep, checked by `test_proof`).
- SQLite (pin `sqlite-src-3540100.zip`, SHA-256 `387e7f6c...c71b`, SHA3-256 matches sqlite.org):
  build `testfixture` with `--coverage`, record `test/select1.test`, map onto the pinned amalgamation.
  - `select1` runs `sqlite3Select` (sqlite3.c:157288 `SQLITE_PRIVATE int sqlite3Select(`; select1.test
    is all `SELECT` statements).
  - **must not**: `select1` runs `jsonExtractFunc` (sqlite3.c:218016) or `sqlite3WindowCodeStep`
    (180412). Proof: select1.test has no `json` and no `OVER` (grep, checked by `test_proof`).
  - The generated `sqlite3.c` equals the pinned amalgamation's (checked by hash in the test).

## 12. Time estimates (agent time)

- Static detection, linking, render, CLI/MCP, fixtures: 4 h.
- Recorder, both gcov parsers, tests.json, staleness, certainty, coverage fixtures: 5 h.
- Golden facts (static + opt-in coverage), README updates, real-output examples: 2 h.
- Total about 11 h. Static part can be a first PR; coverage a second.

User-side cost of `tests record` (measured here, 8 cores):
- Redis: build 31 s; units 3-27 s each (6 measured); 158 units serial roughly 30-60 min, with `--jobs 4`
  roughly 10-20 min; plus about 3 s of gcov per unit.
- SQLite: build 20 s; select1 1 s; the 1,199 test files serial likely 1-2 h (not measured); a
  `veryquick`-sized list is much shorter.

## 13. Risks

- **GNU gcov not tested on this Mac.** CI on Ubuntu runs the coverage fixtures; I will also check
  `gcov --json-format --stdout` there. gcc/gcov version mismatch gives gcov errors we pass through.
- **Lost coverage**: processes killed with SIGKILL, `_exit()` or `abort()` don't write `.gcda`. Redis's
  `COVERAGE_TEST` handles forks; tests that `kill -9` a server lose that server's counts. That can make
  "no test reaches" wrong. Mitigation: wording "none of the recorded tests ran it", a limits line, and
  never "exact" for a partial record.
- **Per-file granularity**: a unit is listed even if one case in it touches the code. Fine for "what to
  run"; said in limits.
- **Config flags vs index flags**: coverage builds use other `-D`s (SQLite's `SQLITE_TEST`, Redis's
  `COVERAGE_TEST`). Functions only in the coverage build are dropped (counted); functions only in the
  index show as "not built with coverage" -> unknown. Line numbers are unaffected.
- **Generated sources** (SQLite amalgamation) map only on equal hash; otherwise counted, with a hint
  about `--linemacros`.
- **The tests write files**: Redis writes into `tests/tmp`. That is the user's own suite, started by the
  user; AeroTrace itself writes only `.aerotrace/tests.json` and temp folders outside the repo.
- **Parallel Redis units** need distinct ports (`{port}`); a config that ignores it may see flaky tests.
- **Static GoogleTest detection** depends on the gtest macro expansion shape; checked against the real
  header in a fixture when `gtest` is installed, otherwise the stub.
- Merging: cli.py / server.py / render.py edits are appended blocks only.

## 14. Needs the owner

1. **Opt-in coverage golden tests run the repos' builds** (Redis `make gcov`, SQLite `configure` +
   `make testfixture`). tests/realrepo/README.md says "We never run a repo's build". Proposal: a separate
   marker `realrepo_coverage`, not in the default `-m realrepo` run; a separate CI job (needs `tcl-dev`
   on Ubuntu), about 2 minutes. OK, or keep coverage facts to the small fixture only?
2. **Pin `sqlite-src-3540100.zip`** (14.2 MiB, hashes above) in `tests/realrepo/repos.py` for that job.
3. **Config location**: `.aerotrace/tests.toml` (proposed) or a committed repo-root file such as
   `aerotrace-tests.toml`, so a team shares one config?
4. **Diff output line** suggesting `aerotrace tests` (section 9). Off unless you say yes.
5. **No MCP record tool** (proposed). Confirm.
6. Later, not in this item: a `probable` link from Redis Tcl tests to command handlers by command name
   (`r psetex` -> `psetexCommand` via `commands.def`), useful without coverage. Worth doing?
