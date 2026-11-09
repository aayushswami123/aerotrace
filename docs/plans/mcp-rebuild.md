# Plan: MCP index rebuilds that keep the agent informed

Status: built. The plan below was reviewed; the **Review** section at the end lists every point and how
it was handled, and wins where the two differ. Branch `feat/mcp-rebuild`.

## Goal

The MCP server builds or rebuilds the index by itself when it is missing, made by another index format, or
when files or compile flags changed (`fresh_index` in `src/aerotrace/index/build.py`). Today:

- The agent gets nothing until the build is done. On a big repo that is minutes of silence.
- If the build fails, the agent sees only `Error executing tool <name>` (the SDK hides the text of any
  exception that is not a `ToolError`).

After this work:

1. While a build runs, the server sends progress: files done out of total.
2. If the build is not done after a short wait, the agent gets a clear answer at once ("still building,
   N of M files, ask again") and the build keeps going in the background.
3. A failed build gives a plain reason and what to do about it.
4. `aerotrace mcp --jobs N` sets the number of parsers. We measure Redis 8.10.3 with 1, 2 and 8 parsers,
   cold and warm cache.

Not changed: the index format (still `SCHEMA_VERSION` 3), the CLI output, what the analysis answers say,
the default number of parsers (CPU count).

## What the installed SDK gives us (mcp 2.3.0)

Checked in `.venv/lib/python3.13/site-packages/mcp`:

- A tool can take a `ctx: Context` argument. `await ctx.report_progress(progress, total, message)` sends
  `notifications/progress` for the current call. It does nothing when the client did not ask for progress
  (no progress token), so it is always safe to call.
- `ctx.log(...)` exists but is marked deprecated ("The logging capability is deprecated as of 2026-07-28
  (SEP-2577)"). We do **not** use it. Server-side log lines go to stderr with Python `logging`, as today.
- Sync tools run in a worker thread (`anyio.to_thread.run_sync`); async tools run on the event loop.
  Requests are handled concurrently (task group in `jsonrpc_dispatcher.py`), so a second call can arrive
  while the first one is waiting.
- `raise ToolError("text")` gives the client `is_error=True` with
  `Error executing tool <name>: text`. Any other exception gives only `Error executing tool <name>`.
- The test client supports `Client.call_tool(..., progress_callback=...)`, so progress can be tested
  in-process.

## Design

### 1. Per-file progress from `build_index`

Add one keyword argument:

```python
def build_index(..., progress: Callable[[Progress], None] = lambda _p: None, isolate: bool = False)

@dataclass(frozen=True)
class Progress:
    phase: str        # "flags", "cache", "parse", "save"
    done: int         # files finished (reused from cache + parsed)
    total: int        # source files in this build
    reused: int       # of `done`, how many came from the cache
```

- Called once after the flags are found (`flags`, 0 of total), once after the cache check (`cache`,
  reused of total), after **each** parsed file (`parse`), and once before writing (`save`, total of total).
- To count files as they really finish, the pool switches from `pool.map(..., chunksize=4)` to one
  `submit` per file and `as_completed`. Results are still stored by file name and merged in the sorted
  unit order, so the index stays byte-for-byte deterministic. The measurement below checks this does not
  slow the build (if it costs more than 5 %, go back to `map` and report progress in submit order).
- `isolate=True` uses a process pool even with 1 job. The MCP server sets it: today, with `jobs == 1`, files
  are parsed inside the server process, so a libclang crash (segfault) would kill the server. The CLI keeps
  `isolate=False` (unchanged).
- The callback is called from the thread that runs `build_index`. It must be quick; the MCP side only
  stores the numbers (see below).
- The CLI does not pass `progress`, so its output is unchanged.

`fresh_index` is split in two so the MCP server can check freshness without building:

- `index_plan(root, build_dir, want) -> RefreshPlan | None`: None when the index is fresh. Otherwise the
  reason ("no index yet", "it was made by another AeroTrace version", "3 changed file(s): a.c, b.c, c.c",
  "compile_commands.json changed", ...), the build dir and excludes to use, and the note to show after.
- `fresh_index` keeps its signature and behaviour (`cli.py` and `tests/test_index.py` use it); it becomes
  `index_plan` + `build_index` + `connect`.

### 2. The MCP side: one background builder and a small state machine

`IndexState` in `src/aerotrace/mcp/server.py` owns at most **one** build thread. State, guarded by one
`threading.Lock`:

```
IDLE ──(call finds index not fresh)──> BUILDING ──(build_index returns)──> IDLE  (+ note for next answer)
                                           │
                                           └──(build_index raises)──────> FAILED (+ reason)
FAILED ──(the next call)──> report the reason once, then IDLE (that call or the one after starts a new try)
```

Fields: `phase`, `done`, `total`, `reused`, `reason` (why we build), `started` (monotonic, for log lines
only), `note` (to prefix on the first answer after a successful build), `error` (failure text), and a
`threading.Event` set when the build ends.

Every tool goes through one async helper, `await state.ready(ctx, want) -> (conn, note)`:

1. Under the lock: if a build is running, skip to step 3.
2. Run `index_plan` in a worker thread (it hashes files, so it must not block the event loop). If the index
   is fresh, open it and return. If not, under the lock start the build thread (only if none is running:
   this is what makes "never two builds at once" true even when several calls arrive together).
3. Wait for the build's `Event` for up to `WAIT_SECONDS` (20 s), waking every 1 s to send
   `ctx.report_progress(done, total, message)`.
4. If the build finished: success means open the new index and return its note; failure means
   `raise ToolError(<failure text>)`.
5. If it is still running after the wait: return the "still building" text (a normal answer, not an
   error) without running the query.

Tool bodies stay sync functions (they run the query in `anyio.to_thread.run_sync`, as the SDK does today);
only the thin wrapper becomes `async def ...(..., ctx: Context)`. The `ctx` argument is not shown to the
agent in the tool schema (the SDK leaves it out), so the tool list does not change.

`index_status` is special: while a build runs, it answers **at once** with the progress text (no 20 s
wait). The "ask again" text points the agent at it.

The build thread is a daemon thread. If the client quits mid-build, the server exits and the half-done
build is dropped; the files parsed so far are already in the parse cache (`_cache_save` runs per file), so
the next build continues from there.

**No stale answers while building.** An old index may exist while we rebuild for changed files. We do not
answer from it: it could miss callers added in the changed files, and a wrong "safe" answer is the worst
bug. We report progress instead. (Considered and rejected: answering from the old index with a warning.)

**Never a half-written index.** Already true: `store.write_index` writes `index.db.tmp<pid>` and swaps it in
with `os.replace`; `store.connect` only ever opens `index.db`; cache entries are written the same way.
Two processes building at once (say the CLI and the MCP server) each write their own temp file and the last
complete one wins. A test pins this (see Tests).

**Files edited during a build.** The next call runs `index_plan` again, and a file that changed after the
parser read it must look changed. (This plan first said "nothing new is needed". That was wrong: the build
took the hash at parse time but the size and mtime at save time, so an edit in between looked fresh
forever, issue #43. Fixed in this PR: the parser records size and mtime together with the hash, checked
against the bytes libclang actually parsed; see "Changes after the PR review".)

### 3. The threshold: 20 s

- This is about the agent's experience, not a client limit: Claude Code's default MCP tool timeout is very
  long (about 1e8 ms; `MCP_TOOL_TIMEOUT` or a per-server `timeout` changes it, and progress does not extend
  it). We make no claim about other clients' limits here. 20 s is long enough for most builds to answer in
  one call and short enough that the agent is never left without news for long.
- Redis 8.10.3 (the redis-server sources) cold on 2 parsers took 9.5 s and on 1 parser 17.9 s (CLI numbers today).
  With 20 s, a cold Redis build answers in the same call even on one core; only bigger repos get the
  "still building" answer. 15 s would push one-core Redis into the background for 3 s of gain.
- Every later call waits up to another 20 s, so an agent that keeps asking is never left with nothing for
  more than about 20 s.
- `WAIT_SECONDS` is a constant; `create_server(..., wait=...)` lets tests use 0.2 s. There is no env var
  or flag for it (one less thing to configure; we can add one if users ask).

### 4. Messages the agent sees, word for word

Numbers use thousands separators (`f"{n:,}"`). There are no times or machine paths in them except the repo
or build folder the user configured, so the same state always gives the same text.

**Progress notification** (`message` field of `notifications/progress`; `progress=done`, `total=total`):

- `Checking which files changed (2,000 files).`  (phase flags/cache)
- `Parsing: 40 of 2,000 files done (12 reused from cache).`  (phase parse)
- `Saving the index (2,000 files).`  (phase save)

**Early answer** (normal text, the build keeps running). `<reason>` is from `index_plan`:

```
AeroTrace is still building the index (<reason>): 40 of 2,000 files done. This may take a few minutes on a
large repo. No answer yet: ask again shortly, or call `index_status` to see progress.
```

Example reasons: `no index yet`, `it was made by another AeroTrace version`, `3 changed file(s): a.c,
b.c, c.c`, `compile_commands.json changed`. Before the parse phase starts, the counts part reads
`checking which files changed`; in the save phase it reads `all 2,000 files done, saving`.

**`index_status` while building:** the same text without "No answer yet: ...", ending with
`Ask again shortly.`

**After a successful background build**, the first answer starts with the note we have today, for example
`(Built the index: 5,123 functions from 2,000 files.)` or `(Rebuilt the index: <reason>. Now ...)`.

**Failures** (`raise ToolError`, so the agent sees `Error executing tool <name>: ` followed by):

| Cause (how we detect it) | Text |
|---|---|
| No sources (`SystemExit` from `build_index`, "no C or C++ source files") | `Could not build the index: no C or C++ source files found under <root>. Start the AeroTrace MCP server in the project folder, or pass --root.` |
| `--build-dir` has no compile_commands.json (`FileNotFoundError` from `compdb`) | `Could not build the index: no compile_commands.json in <dir>. Fix --build-dir in the MCP settings, or create it (CMake: cmake -B <dir> -DCMAKE_EXPORT_COMPILE_COMMANDS=ON).` |
| `$AEROTRACE_LIBCLANG` points to a missing file (`FileNotFoundError` from `libclang`) | `Could not build the index: AEROTRACE_LIBCLANG points to <path>, which does not exist. Fix or unset it.` |
| libclang cannot be loaded (`clang.cindex.LibclangError`, or `OSError` while loading it) | `Could not build the index: libclang could not be loaded (<first line of the error>). Install LLVM, or set AEROTRACE_LIBCLANG to the libclang library file.` |
| A parser process died (`BrokenProcessPool`) | `Could not build the index: a parser process crashed, most likely libclang on one of these files: a.c, b.c (up to 5, sorted). Files parsed so far are cached. Ask again to retry; if it crashes again, run "aerotrace index --jobs 1" in a terminal and report the bug.` |
| Disk full (`OSError` with `errno.ENOSPC`, or `sqlite3` "database or disk is full") | `Could not build the index: the disk is full (writing <repo>/.aerotrace). Free some space and ask again.` |
| No permission (`PermissionError`) | `Could not build the index: no permission to write <path>. AeroTrace keeps its index in <repo>/.aerotrace.` |
| Out of memory (`MemoryError`) | `Could not build the index: out of memory. Try fewer parsers: "aerotrace mcp --jobs 2".` |
| Anything else | `Could not build the index: unexpected error (<ExceptionType>: <first line of message>). The full error is in the MCP server log (stderr). Please report it as a bug.` |

"Files that might have crashed" are the futures still marked running when the pool broke (at most a few
per parser). Every failure is also logged to stderr with the traceback.

After a failure is reported, the next call starts a fresh try. This is cheap thanks to the parse cache, and
it lets the agent or user fix the cause (create compile_commands.json, free disk) without restarting the
server. A cause that fails at once (no libclang) costs well under a second per try.

### 5. Server-side log lines (stderr)

`aerotrace: building the index (<reason>), 2,000 files, 8 parsers`, then at most one line per 25 % of
files, then `aerotrace: index built in 41 s` or `aerotrace: index build failed: <text>`. Level WARNING so
they show with today's logging setup. Stdout stays the MCP wire only.

### 6. `aerotrace mcp --jobs N`

New flag, same help text as `aerotrace index --jobs` ("parallel parsers (default: CPU count)"), passed to
`create_server(..., jobs=N)` and on to `build_index`. `aerotrace install` is not changed (users can add the
flag by hand); the README's MCP section gets one line about it.

## Tests

All in-process with the SDK `Client`, on the existing fixtures, so they run in the normal `pytest`:

1. `build_index` progress (`tests/test_index.py`): on `c_multi`, cold cache: phases come in order, `done`
   never goes down, the last call is `done == total == units`; warm cache: `reused == total` and no parse
   calls. The index written is identical to one built without a callback (compare table dumps).
2. Same as 1 with `jobs=2` and with `isolate=True, jobs=1`.
3. CLI unchanged: existing `tests/test_cli.py` passes untouched; plus a check that `aerotrace index` stderr
   has no progress lines.
4. **Slow fake build (the early answer).** `create_server(root, wait=0.2, build=fake)` where `fake` calls
   `progress` for "3 of 10" and then blocks on a `threading.Event`:
   - first `impact` call returns within 2 s with the exact early-answer text, `3 of 10`;
   - a second call while blocked returns the progress text again; `index_status` returns at once;
   - five calls started together (`anyio` task group) all return, and `fake` ran exactly once;
   - release the event; the next call gives the real answer with the `(Built the index: ...)` note.
5. Progress notifications: a real build of `c_multi` with `progress_callback` set; at least one
   notification, the last has `progress == total`, every `message` matches the patterns above.
6. Failures: `fake` raising each mapped exception gives `is_error` and the exact text. Real cases too:
   `--build-dir` pointing at an empty folder, `AEROTRACE_LIBCLANG` set to a missing file (monkeypatch env),
   an empty repo. After a failure, the next call runs `fake` again (counter == 2).
7. Never half an index: while `fake` is blocked after writing a junk `index.db.tmp<pid>`, no call opens it
   and `index.db` (absent or old) is not served; existing `write_index` tests keep covering the swap.
8. Stale index not served: build once, change a file, block the rebuild with `fake`: the answer is the
   progress text, not the old result.
9. Parser crash: `BrokenProcessPool` via `fake` (message test). A real crash in a spawned worker cannot be
   patched in from the test process, so there is no real-crash test; stated in the PR.

Before every commit: `uv run ruff check . && uv run ruff format --check . && uv run pytest`. Before the PR:
`uv run pytest -m realrepo -rxX` (must stay at the same 3 known failures, #12 and #13).

## Measurement plan

Script in my own scratch folder (not in the repo, run from a file because of `spawn`), with the repo's
`tests/realrepo/repos.py`: `unpack(REDIS, <my temp dir>)` from `~/.cache/aerotrace-realrepo`, then
`write_compdb`. For jobs = 1, 2, 8, three runs each, median:

| Run | How |
|---|---|
| CLI cold | delete `.aerotrace/`, `aerotrace index --jobs N` (wall time) |
| CLI warm | keep the cache, delete `index.db`, same command |
| MCP cold / warm | in-process `Client`, `create_server(root, jobs=N)`, first `index_status` call: time to first reply, time until the index is built, number of progress notifications |
| MCP no-op | second call on a fresh index (freshness check cost) |

Run the CLI rows on `origin/launch-candidate` and on the branch, to show `as_completed` + progress cost
nothing (target: within 5 %, the run-to-run noise). Also check the MCP index is identical to the CLI one.
Known today on 8 cores: warm 1.4 s, cold 4.9 s; CLI cold 9.5 s on 2 jobs, 17.9 s on 1. The PR reports the
full table and the machine (CPU, core count).

To see the early answer on a real repo, one extra run with `wait=2` on 1 parser cold: first reply in about
2 s with the progress text, a later call gives the answer.

## Risks

- **`as_completed` slower than `map(chunksize=4)`** (more round trips per file). Measured; fall back if so.
- **A process pool for 1 job in the MCP server** adds start-up time (spawn imports libclang, about 0.5 s).
  Measured; worth it so a libclang crash cannot kill the server.
- **Server exits mid-build**: spawned workers finish their current file and exit; a `index.db.tmp<pid>`
  file may stay behind. It is never read. Small follow-up (not in this item unless the reviewer wants it):
  `write_index` removes temp files of processes that no longer run.
- **Two MCP servers on one repo** (two agents) can both build at once. Safe (atomic swap), just double the
  CPU. A cross-process lock file is out of scope.
- **Clients without progress support** just get the early answer text; nothing breaks.
- **An agent that does not ask again** gets no answer, but it was told clearly why; better than a timeout.
- **Freshness check on every call** is unchanged from today (it already hashes changed files); it now runs
  in a worker thread so it cannot stall progress for other calls.
- **SDK API drift**: `Context.report_progress` is in mcp 2.3; `pyproject.toml` pins `mcp>=2.3,<3`.

Nothing here needs an index format change or a new dependency.

## Review

An independent reviewer checked the plan against mcp 2.3.0. Verdict: go with changes. Every point, and
what was done:

| # | Point | How it was handled |
|---|---|---|
| 1 | MUST: open, use and close each sqlite connection in one thread | `answer()` in `server.py` runs `store.connect` + the query + close inside one `anyio.to_thread.run_sync` call. `index_plan` opens and closes its own connection. |
| 2 | MUST: a finished build may not cover the waiting call | `IndexState.ready()` loops: plan, start or join the build, wait, plan again, until `index_plan` says fresh or the 20 s budget runs out. Wanted files that the last build could not index (excluded) end the loop, and at most 3 builds run per call (then the answer says it may miss the latest edits). Test: `test_finished_build_that_misses_new_edits_is_followed_by_another`. |
| 3 | MUST: a failure must reach every waiter | One `Build` object per attempt (event, progress, error, note). Each waiter holds its own `Build`. No FAILED state, no global error; the next new call starts a new attempt. Test: `test_build_failure_reaches_every_waiting_call` (two waiters get the same error, the build ran once, the next call builds again). |
| 4 | MUST: exit promptly mid-build | A stop event goes into `build_index`; the pool loop checks it every 0.5 s, terminates the parser processes, then `shutdown(wait=False, cancel_futures=True)` (the processes are taken first: `shutdown` forgets them). `run()` sets it when the server returns (stdin closed); SIGINT/SIGTERM stop the build and `os._exit`, because a normal unwind waits forever for the thread blocked on stdin. Test (subprocess): close stdin, or SIGTERM, mid-build: exit in under 3 s (measured 0.1-0.3 s), no parser process left, no index written. |
| 5 | SHOULD: name failures instead of guessing from exception types | `index_plan` checks cheap causes before building (`--build-dir` has compile_commands.json, `AEROTRACE_LIBCLANG` exists) and raises `SetupError`. `build_index` raises `NoSourcesError(SystemExit)` (CLI unchanged), `LibclangLoadError`, `ParserCrashed`. The thread catches `BaseException`. The crash text says "crashed or ran out of memory". Disk full, permission and memory errors are still matched by type (errno ENOSPC, sqlite "disk is full", `PermissionError`, `MemoryError`); everything else is "unexpected error (Type: message)". |
| 6 | SHOULD: save finished results on a crash | Results that finished before the pool broke are handed to `finished()`, which writes them to the cache, before `ParserCrashed` is raised. Test with a worker that kills itself on one file. |
| 7 | SHOULD: keep refusing old answers; measure an incremental rebuild | Kept. Measured below (one .c file changed, and `server.h` changed, warm cache, 1 parser). |
| 8 | SHOULD: wait asynchronously; a cancelled call must not stop the build | `while not finished: await anyio.sleep(...)`. Test: `test_cancelled_call_does_not_stop_the_build`. |
| 9 | SHOULD: no redundant builds | A generation counter: a call that planned while another build finished plans again instead of starting a second build. |
| 10 | NIT: determinism | `build_index` refuses to write when a unit has no result. `dump_canonical` of a CLI build and an MCP build of Redis compared (identical, see below); the fixture tests compare with and without progress / pool. |
| 11 | NIT: does `Outliner` (diff_impact) parse in the server process? | Yes: `history.Outliner` parses old file versions with libclang in-process. The process isolation covers index builds only. |
| 12 | NIT: wording | "1, 2 and 8 parallel parsers on one 8-core machine" (macOS cannot pin cores). |
| 13 | NIT: temp-file cleanup | Issue #36. |
| 14 | NIT: tests for 2, 3, 4, 8; tool schemas unchanged | Tests above. `test_tool_schemas_are_unchanged` compares the tool list with `tests/golden/mcp_tool_schemas.json`, captured from launch-candidate before the change: `ctx` does not show. |

Other changes while building:

- The parser-crash text no longer guesses which file crashed. A pool cannot tell which future was running
  when a process died, so it lists how many files were not parsed and the first few of them.
- A successful build's note ("(Built the index: ...)") is shown once even if every waiting call timed out:
  the next answer carries it.
- With 20 s, the agent sees the threshold only on big repos; the README says what happens.
- Found an earlier bug, filed as #35: with guessed flags, a diff touching a new file in an excluded folder
  re-indexes on every call.
- A waiting call checks for the end of the build every 0.05 s (progress still goes out every 1 s); with a
  1 s check, every answer came up to a second late.

## Measurements (as built)

Redis 8.10.3 (117 files of redis-server), 1, 2 and 8 parallel parsers on one 8-core machine (Apple M1
Pro, macOS). The machine was shared with other jobs (load average 3 to 15), so base CLI, branch CLI and
MCP runs were interleaved, 3 rounds; medians in seconds. CLI = `aerotrace index --jobs N` wall time,
including Python start-up. MCP = time from the first `index_status` call to its answer, in-process client.
Cold = no `.aerotrace/`; warm = cache kept, `index.db` deleted; one .c = `src/t_string.c` changed;
server.h = `src/server.h` changed (included by most files).

| Parsers | Case | CLI on launch-candidate | CLI on this branch | MCP on this branch |
|---|---|---|---|---|
| 8 | cold | 4.9 | 4.4 | 5.0 |
| 8 | warm | 1.3 | 1.3 | 1.4 |
| 8 | one .c | 1.5 | 1.5 | 2.0 |
| 8 | server.h | 4.5 | 4.2 | 4.9 |
| 2 | cold | 9.3 | 9.3 | 9.7 |
| 2 | warm | 1.3 | 1.3 | 1.4 |
| 2 | one .c | 1.6 | 1.5 | 2.0 |
| 2 | server.h | 9.0 | 8.9 | 9.4 |
| 1 | cold | 18.3 | 18.6 | 18.1 |
| 1 | warm | 1.3 | 1.4 | 1.4 |
| 1 | one .c | 1.6 | 1.5 | 2.0 |
| 1 | server.h | 16.1 | 16.0 | 16.2 |

- No slowdown from `as_completed` + progress (branch CLI vs base CLI: within noise).
- Every MCP build of Redis answered in the same call (all under 20 s), even cold on 1 parser (18.1 s; the
  slowest run was 18.8 s, close to the limit). A busier or slower machine gets the early answer instead.
- The MCP's extra ~0.5 s for a one-file change is starting the parser process (`isolate=True`); merge and
  write of the whole index take about 1 s on Redis, so they are far from the 20 s limit here. On a repo
  10 times bigger they could take about 10 s on their own; the early answer covers that case.
- A no-op call (index fresh) costs about 4 ms.
- Early answer, real repo: 1 parser, cold, wait set to 2 s: the first call answered after 2.0 s with
  "AeroTrace is still building the index (no index yet): 8 of 117 files done. ...", and later calls got
  the answer.
- A CLI build (1 parser, no pool, no cache) and an MCP build (8 parsers) of Redis give the same
  `dump_canonical` (126,971 lines, identical).
- Quitting mid-build (stdin closed or SIGTERM): the server exits in 0.1-0.3 s, parser processes gone.

## Changes after the PR review

- **#43 (bug in earlier code, fixed here as its own commit):** a file edited between parse and save was
  recorded with its old hash but its new size and mtime, so `changed_files` (which trusts an unchanged size
  and mtime) never looked at it again: a wrong "safe" answer. Now `parse.file_stamp` takes size, mtime and
  hash together (stat, hash, stat), and compares the hash with the bytes libclang parsed
  (`clang_getFileContents`). If they differ, or the file changes while being hashed, it records hash ""
  and mtime -1, which never match, so the next check re-indexes it. A cache hit takes fresh stamps the same
  way. A file deleted during the build no longer fails the build. `PARSER_VERSION` is 11 (old cache entries
  are parsed again once); the index format is unchanged.
- Parser processes ignore SIGINT (Ctrl-C in a terminal reaches the whole process group; the server ends
  them itself), and the server releases the pool's semaphores before it exits on a signal: no tracebacks
  (tested with SIGINT to the process group). A "leaked semaphore" warning from multiprocessing's
  resource tracker can still show now and then; it is harmless, the tracker removes them.
- A note is no longer lost: if a call finishes one build and the next build runs past the wait, the answer
  is the first build's note followed by the "still building" text. Whether a note was shown is read and
  written under the lock.
- Left as is: the save phase (merge and write, about 1 s on Redis) does not check the stop request. Exit
  waits for it (at most 5 s after a signal); only a temp file can be left behind (#36).
