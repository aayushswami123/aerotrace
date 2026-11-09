# Plan: MCP index rebuilds that keep the agent informed

Status: plan, waiting for review. Branch `feat/mcp-rebuild`.

## Goal

The MCP server builds or rebuilds the index by itself when it is missing, made by another index format, or
when files or compile flags changed (`fresh_index` in `src/aerotrace/index/build.py`). Today:

- The agent gets nothing until the build is done. On a big repo that is minutes, and many clients give up
  on a tool call after about 60 s.
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

**Files edited during a build.** The next call runs `index_plan` again; if a file changed after the parser
read it, its hash no longer matches and a new build starts. Nothing new is needed.

### 3. The threshold: 20 s

- Most clients cut a tool call at about 60 s unless they get progress (the official TypeScript SDK's default
  request timeout is 60 s and only resets on progress when the client opts in; Codex's default tool timeout
  is 60 s; both from memory, to be checked against their docs before the PR).
  20 s leaves a wide margin for the query that runs after the build and for slow machines.
- Redis 8.10.3 (the redis-server sources) cold on 2 parsers took 9.5 s and on 1 parser 17.9 s (CLI numbers today).
  With 20 s, a cold Redis build answers in the same call even on one core; only bigger repos get the
  "still building" answer. 15 s would push one-core Redis into the background for 3 s of gain.
- Every later call waits up to another 20 s, so an agent that keeps asking is never left with nothing for
  more than 20 s, and no single call comes close to 60 s.
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
