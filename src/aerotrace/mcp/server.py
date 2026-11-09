"""The MCP server: a thin layer over `aerotrace.analysis`. Agents start it with `aerotrace mcp`.

It keeps the index fresh by itself: before every answer it checks whether indexed files changed, and if
so re-indexes (only changed files are re-parsed). A long build runs in the background: the call waits up
to WAIT_SECONDS, sending progress, and otherwise answers "still building". Nothing is printed to stdout,
which is the MCP wire.
"""

from __future__ import annotations

import errno
import logging
import os
import signal
import sqlite3
import threading
import time
from collections.abc import Callable
from concurrent.futures.process import BrokenProcessPool
from contextlib import closing
from pathlib import Path

import anyio
import anyio.to_thread
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError

from aerotrace import __version__
from aerotrace.analysis import data, diff, graph, render, render_data, signature, symbols, testimpact
from aerotrace.index import store
from aerotrace.index.build import (
    BuildReport,
    BuildStopped,
    LibclangLoadError,
    NoSourcesError,
    ParserCrashed,
    Progress,
    RefreshPlan,
    SetupError,
    build_index,
    index_plan,
)
from aerotrace.index.history import Outliner
from aerotrace.toolchain.git import GitError

log = logging.getLogger("aerotrace.mcp")

# How long one tool call waits for a build before it answers "still building". Long enough that a cold
# build of a mid-size repo (Redis on one parser: about 18 s) still answers in the same call, short enough
# that an agent is never left waiting long without news. See docs/plans/mcp-rebuild.md.
WAIT_SECONDS = 20.0
TICK_SECONDS = 1.0  # how often a waiting call sends progress
POLL_SECONDS = 0.05  # how often a waiting call checks whether the build is done
MAX_BUILDS_PER_CALL = 3  # files that keep changing during builds must not keep one call busy forever

INSTRUCTIONS = """\
AeroTrace knows how this C/C++ codebase fits together: who calls what, through direct calls, virtual calls
and function pointers, and which globals and struct fields each function reads and writes. It runs locally
and its answers are deterministic.

Use it BEFORE you edit C or C++ code:
- Before changing a function, call `impact` on it. Read the affected callers and the "Limits of this answer".
- After editing, or before applying a patch, call `diff_impact` to check the whole change: changed and
  deleted functions, changed globals and fields, and everything upstream of them.
- Before changing a function's parameters or return type, call `signature_impact`: it lists every call
  site and how each one uses the return value.
- Before renaming, removing or retyping a struct field or a global, call `data_users`: every function that
  reads or writes it, initializers that set it, code that still uses a removed field, and where its address
  goes. Fields are tracked per struct type, not per object.
- To pick tests to run for a change, call `tests_for_change` (same inputs as `diff_impact`).
- Use `callers` / `callees` to follow the code, and `find` when you are not sure of a name.
- Labels: no label = a direct call; [virtual] = may land on this override; [via_pointer] = matched by
  function-pointer type; [ambiguous] = several functions share this name; [data] = reads data the changed
  function writes; [uses_changed] = uses a global or field whose declaration changed; [calls_removed] =
  still calls a function the change removes. Treat labelled links as real possibilities, not proof.
- If a name matches several functions, ask again with `name@path/to/file.c` or `file.c:LINE`.
"""


# ---------------------------------------------------------------- messages


def _counts(p: Progress) -> str:
    if p.phase == "parse":
        return f"{p.done:,} of {p.total:,} files done"
    if p.phase == "save":
        return f"all {p.total:,} files done, saving"
    return "checking which files changed" if p.total else "finding the source files"


def progress_text(p: Progress) -> str:
    """The message of a progress notification."""
    if p.phase == "parse":
        return f"Parsing: {p.done:,} of {p.total:,} files done ({p.reused:,} reused from cache)."
    if p.phase == "save":
        return f"Saving the index ({p.total:,} files)."
    if p.total:
        return f"Checking which files changed ({p.total:,} files)."
    return "Finding the source files and compile flags."


def busy_text(reason: str, p: Progress, *, status: bool = False) -> str:
    """The answer while a build is still running."""
    head = (
        f"AeroTrace is still building the index ({reason}): {_counts(p)}. "
        "This may take a few minutes on a large repo."
    )
    if status:
        return f"{head} Ask again shortly."
    return f"{head} No answer yet: ask again shortly, or call `index_status` to see progress."


def failure_text(e: BaseException, root: Path) -> str:
    """Why a build failed, and what to do, in one or two sentences."""
    where = root / store.DIR_NAME
    if isinstance(e, NoSourcesError):
        why = (
            f"no C or C++ source files found under {root}. "
            "Start the AeroTrace MCP server in the project folder, or pass --root."
        )
    elif isinstance(e, SetupError):
        why = str(e)
    elif isinstance(e, LibclangLoadError):
        why = (
            f"libclang could not be loaded ({e}). "
            "Install LLVM, or set AEROTRACE_LIBCLANG to the libclang library file."
        )
    elif isinstance(e, ParserCrashed | BrokenProcessPool):
        on = ""
        if isinstance(e, ParserCrashed) and e.files:
            more = ", ..." if e.unfinished > len(e.files) else ""
            on = f" ({e.unfinished:,} files not parsed yet, the one it crashed on among them: {', '.join(e.files)}{more})"
        why = (
            f"a parser process crashed or ran out of memory{on}. Files parsed so far are cached. "
            'Ask again to retry; if it fails again, run "aerotrace index --jobs 1" in a terminal '
            "and report the bug."
        )
    elif (isinstance(e, OSError) and e.errno == errno.ENOSPC) or (
        isinstance(e, sqlite3.Error) and "disk is full" in str(e)
    ):
        why = f"the disk is full (writing {where}). Free some space and ask again."
    elif isinstance(e, PermissionError):
        why = f"no permission to write {e.filename or where}. AeroTrace keeps its index in {where}."
    elif isinstance(e, MemoryError):
        why = 'out of memory. Try fewer parsers: "aerotrace mcp --jobs 2".'
    elif isinstance(e, BuildStopped):
        why = "the AeroTrace server is shutting down."
    elif isinstance(e, FileNotFoundError):
        why = f"{e}."
    else:
        first = (str(e).strip().splitlines() or [""])[0]
        why = (
            f"unexpected error ({type(e).__name__}{': ' + first if first else ''}). "
            "The full error is in the MCP server log (stderr). Please report it as a bug."
        )
    return f"Could not build the index: {why}"


# ---------------------------------------------------------------- the index, built in the background


class Build:
    """One build attempt. Every call waiting for it reads its result from here."""

    def __init__(self, plan: RefreshPlan, want: tuple[str, ...]):
        self.plan = plan
        self.want = want
        self.progress = Progress("flags", 0, 0, 0)  # replaced as a whole, so readers see one state
        self.finished = threading.Event()
        self.error: BaseException | None = None
        self.failure = ""  # the text for the agent when it failed
        self.note = ""  # the text for the agent when it worked
        self.shown = False  # whether some answer carried the note


BuildFn = Callable[..., BuildReport]


class IndexState:
    """Keeps the index fresh for the tools. At most one build runs at a time, in a background thread; a
    call waits for it up to `wait` seconds and otherwise answers with progress. The tools open, use and
    close their index connection inside one worker thread (sqlite needs that)."""

    def __init__(
        self,
        root: Path,
        build_dir: Path | None = None,
        *,
        jobs: int | None = None,
        wait: float = WAIT_SECONDS,
        build: BuildFn = build_index,
    ):
        self.root = root
        self.build_dir = build_dir
        self.jobs = jobs
        self.wait = wait
        self._build = build
        self._lock = threading.Lock()
        self._current: Build | None = None
        self._generation = 0  # counts finished builds, so a plan made before one finished is redone
        self._thread: threading.Thread | None = None
        self._last: Build | None = None  # the last build that worked, to show its note once
        self.stop = threading.Event()

    # -- the build thread

    def _start(self, plan: RefreshPlan, want: tuple[str, ...]) -> Build:
        """Start a build. Call with the lock held and no build running."""
        b = Build(plan, want)
        self._current = b
        self._thread = threading.Thread(target=self._run, args=(b,), name="aerotrace-index", daemon=True)
        self._thread.start()
        return b

    def _run(self, b: Build) -> None:
        started = time.monotonic()
        quarter = 0
        log.warning("building the index (%s)", b.plan.reason)

        def on_progress(p: Progress) -> None:
            nonlocal quarter
            b.progress = p
            if p.phase == "parse" and p.total and p.done * 4 // p.total > quarter:
                quarter = p.done * 4 // p.total
                log.warning("%s of %s files done", f"{p.done:,}", f"{p.total:,}")

        try:
            report = self._build(
                self.root,
                build_dir=b.plan.build_dir,
                excludes=b.plan.excludes,
                jobs=self.jobs,
                log=log.info,
                progress=on_progress,
                isolate=True,  # a libclang crash must not take the server down
                stop=self.stop,
            )
            b.note = b.plan.note(report)
            log.warning("index built in %.0f s", time.monotonic() - started)
        except BaseException as e:  # every failure must reach the waiting calls
            b.error = e
            b.failure = failure_text(e, self.root)
            if not isinstance(e, BuildStopped):  # the traceback only when we could not explain it
                log.warning(
                    "index build failed: %s", b.failure, exc_info=e if "unexpected" in b.failure else None
                )
        finally:
            with self._lock:
                self._current = None
                self._generation += 1
                if b.error is None:
                    self._last = b
            b.finished.set()

    def _take_note(self, b: Build | None) -> str:
        """The build's note, the first time any answer asks for it ("" after that)."""
        with self._lock:
            if b is None or b.shown:
                return ""
            b.shown = True
            return b.note

    def shutdown(self, timeout: float = 10.0) -> None:
        """Stop a running build (its parser processes end at once) and wait a little for it."""
        self.stop.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout)

    # -- what the tools call

    async def ready(
        self, ctx: Context | None, want: list[str] | tuple[str, ...] = (), *, wait_if_busy: bool = True
    ) -> tuple[str | None, str]:
        """Make sure the index is fresh for this call: (text to answer instead, or None; note).

        Loops (plan, build or join the running build, wait, plan again), because a finished build may
        not cover this call: it was started for other files, or files changed while it ran."""
        want = tuple(want)
        deadline = time.monotonic() + self.wait
        note = ""
        last: Build | None = None
        builds = 0
        while True:
            with self._lock:
                b, gen = self._current, self._generation
            if b is not None and not wait_if_busy:
                return busy_text(b.plan.reason, b.progress, status=True), note
            if b is None:
                try:
                    plan = await anyio.to_thread.run_sync(
                        lambda: index_plan(self.root, build_dir=self.build_dir, want=want)
                    )
                except Exception as e:
                    raise ToolError(failure_text(e, self.root)) from e
                if plan is None or _covered(plan, last):
                    if last is None:  # a build nobody saw finish (every waiting call timed out)
                        with self._lock:
                            unseen = self._last
                        note = self._take_note(unseen)
                    return None, note
                if builds >= MAX_BUILDS_PER_CALL:
                    return None, (
                        "(Files kept changing while the index was rebuilt; this answer may miss the "
                        "latest edits.)"
                    )
                with self._lock:
                    if self._current is None and self._generation == gen:
                        b = self._start(plan, want)
                    elif self._current is not None:
                        b = self._current
                    else:
                        continue  # a build finished while we planned: plan again
            next_report = 0.0
            while not b.finished.is_set():
                now = time.monotonic()
                if now >= deadline:
                    return busy_text(b.plan.reason, b.progress), note
                if now >= next_report:
                    await _report(ctx, b.progress)
                    next_report = now + TICK_SECONDS
                await anyio.sleep(min(POLL_SECONDS, deadline - now))
            if b.error is not None:
                raise ToolError(b.failure) from None
            note, last, builds = self._take_note(b) or note, b, builds + 1


def _covered(plan: RefreshPlan, last: Build | None) -> bool:
    """True when the only reason left is wanted files that the last build was asked for and could not
    index (for example excluded files): building again would not change anything."""
    return (
        last is not None
        and plan.kind == "files"
        and not plan.changed
        and bool(plan.missing)
        and set(plan.missing) <= set(last.want)
    )


async def _report(ctx: Context | None, p: Progress) -> None:
    if ctx is None:
        return
    try:
        await ctx.report_progress(p.done, p.total or None, progress_text(p))
    except Exception:  # the client went away: the build goes on anyway
        log.debug("could not send progress", exc_info=True)


def _with_note(text: str, note: str) -> str:
    return f"{note}\n{text}" if note else text


# ---------------------------------------------------------------- the tools


def _create(
    root: Path,
    build_dir: Path | None = None,
    *,
    jobs: int | None = None,
    wait: float = WAIT_SECONDS,
    build: BuildFn = build_index,
) -> tuple[MCPServer, IndexState]:
    state = IndexState(root, build_dir, jobs=jobs, wait=wait, build=build)
    server = MCPServer("aerotrace", instructions=INSTRUCTIONS, version=__version__)

    async def answer(
        ctx: Context | None,
        body: Callable[[sqlite3.Connection], str],
        want: list[str] | tuple[str, ...] = (),
        *,
        wait_if_busy: bool = True,
    ) -> str:
        busy, note = await state.ready(ctx, want, wait_if_busy=wait_if_busy)
        if busy is not None:  # keep the note of a build that did finish during this call
            return _with_note(busy, note)

        def run() -> str:  # open, query and close in one thread
            with closing(store.connect(root)) as conn:
                return _with_note(body(conn), note)

        return await anyio.to_thread.run_sync(run)

    def resolve(conn: sqlite3.Connection, symbol: str) -> tuple[symbols.Fn | None, str]:
        fns = symbols.find(conn, symbol)
        if len(fns) == 1:
            return fns[0], ""
        return None, render.candidates_text(symbol, fns or symbols.search(conn, symbol))

    def about(symbol: str, show: Callable[[sqlite3.Connection, symbols.Fn], str]):
        def body(conn: sqlite3.Connection) -> str:
            fn, msg = resolve(conn, symbol)
            return msg if fn is None else show(conn, fn)

        return body

    @server.tool()
    async def impact(symbol: str, depth: int = 5, limit: int = 40, ctx: Context | None = None) -> str:
        """What could break if this C/C++ function changes: every caller up to `depth` steps away (direct,
        virtual and function-pointer calls), functions that read globals or fields it writes, and the
        limits of the answer. `symbol` is a name, ns::Class::method, name@file, or file.c:LINE."""
        return await answer(
            ctx, about(symbol, lambda conn, fn: render.impact_text(graph.impact(conn, fn, depth), limit))
        )

    @server.tool()
    async def diff_impact(
        base: str = "HEAD", patch: str = "", depth: int = 5, limit: int = 40, ctx: Context | None = None
    ) -> str:
        """What a whole change could break. With no arguments: your uncommitted edits compared with HEAD
        (new files included). `base="main"`: everything on this branch plus uncommitted edits, compared with
        where the branch left main. `patch="<unified diff>"`: a change you have NOT applied yet; its old
        side must match the files on disk. Lists changed and removed functions, changed globals and fields,
        every affected caller, and the changes it could not follow."""
        outliner = Outliner(root, build_dir)

        def load():
            if patch:
                return "patch", None, diff.load_patch(root, patch), {}
            diffs, desc, old = diff.load_git(root, base, outliner)
            return "git", desc, diffs, old

        try:
            mode, desc, diffs, old = await anyio.to_thread.run_sync(load)
        except (GitError, ValueError) as e:
            return f"Could not read the change: {e}"

        def body(conn: sqlite3.Connection) -> str:
            res = diff.analyse(
                conn,
                root,
                diffs,
                mode=mode,
                base=desc,
                depth=depth,
                old=old,
                now=outliner.now,
                patched=outliner.text,
            )
            return render.diff_text(res, limit)

        return await answer(ctx, body, diff.wanted(diffs, mode))

    @server.tool()
    async def signature_impact(
        symbol: str,
        depth: int = 2,
        limit: int | None = None,
        show_all: bool = False,
        ctx: Context | None = None,
    ) -> str:
        """Before changing a function's parameters (add, remove, reorder, retype) or its return type: every
        call site with its argument count and how it uses the return value (stored, compared, passed on,
        ignored, ...), every place that stores the function's address (tables, callbacks), pointer calls that
        may reach it, and callers further up, grouped by the caller they come through. Each result says
        exact / probable and why. A summary comes first; call sites that do not compile come first and are
        never cut. Lists are short by default (about 15) and say how many were left out: pass `limit` for
        more per list, or `show_all=true` for everything."""
        if limit is not None and limit < 1:
            raise ToolError("limit must be 1 or more.")
        if limit is not None and show_all:
            raise ToolError("Pass either limit or show_all, not both.")

        def show(conn: sqlite3.Connection, fn: symbols.Fn) -> str:
            res = signature.signature_impact(conn, fn, depth)
            return render.signature_text(res, limit, show_all, render.MCP_MORE)

        return await answer(ctx, about(symbol, show))

    @server.tool()
    async def data_users(
        name: str,
        depth: int | None = None,
        from_function: str = "",
        limit: int | None = None,
        show_all: bool = False,
        ctx: Context | None = None,
    ) -> str:
        """Before renaming, removing or retyping a struct field or a global: every function that reads or
        writes it (with lines), initializers that set it ({.f = x}), code that still uses a removed field,
        globals of its type that may set it by position, and where its address goes (unknown). For a global,
        also its users' callers (depth, default 2; fields 0). `name` is Struct::field, global, name@file or
        global.field. With `from_function`, only what reads the value that function writes (a field's readers
        are probable: fields are tracked per struct type, not per object). A summary comes first; lists are
        short by default (about 15) and say how many were left out: pass `limit`, or `show_all=true`."""
        if limit is not None and limit < 1:
            raise ToolError("limit must be 1 or more.")
        if limit is not None and show_all:
            raise ToolError("Pass either limit or show_all, not both.")

        def body(conn: sqlite3.Connection) -> str:
            found = data.find_variables(conn, name) or data.gone_fields(conn, name)
            if len(found) != 1:
                return render_data.candidates_text(name, found, near=data.suggest(conn, name))
            writer = None
            if from_function:
                writer, msg = resolve(conn, from_function)
                if writer is None:
                    return msg
            res = data.data_users_of(conn, found[0], depth, writer.id if writer else None)
            return render_data.data_text(res, limit, show_all, render_data.MCP_MORE)

        return await answer(ctx, body)

    @server.tool()
    async def tests_for_change(
        base: str = "HEAD",
        patch: str = "",
        symbol: str = "",
        depth: int = 8,
        limit: int = 30,
        ctx: Context | None = None,
    ) -> str:
        """Which tests to run for a change, why, and how sure (exact / probable), plus the changed code no
        known test reaches. Same inputs as `diff_impact` (no arguments = your uncommitted edits), or
        `symbol` for one function. It never runs tests. "Not known" means AeroTrace can't tell, not that
        the code is untested."""
        if symbol:

            def one(conn: sqlite3.Connection, fn: symbols.Fn) -> str:
                return render.tests_text(testimpact.for_function(conn, root, fn, depth), limit)

            return await answer(ctx, about(symbol, one))
        outliner = Outliner(root, build_dir)

        def load():
            if patch:
                return "patch", None, diff.load_patch(root, patch), {}
            diffs, desc, old = diff.load_git(root, base, outliner)
            return "git", desc, diffs, old

        try:
            mode, desc, diffs, old = await anyio.to_thread.run_sync(load)
        except (GitError, ValueError) as e:
            return f"Could not read the change: {e}"

        def body(conn: sqlite3.Connection) -> str:
            d = diff.analyse(conn, root, diffs, mode=mode, base=desc, depth=depth, old=old, now=outliner.now)
            return render.tests_text(testimpact.for_diff(conn, root, d, depth), limit)

        return await answer(ctx, body, diff.wanted(diffs, mode))

    @server.tool()
    async def callers(symbol: str, depth: int = 3, limit: int = 40, ctx: Context | None = None) -> str:
        """Functions that call this one, directly or through other functions, up to `depth` steps."""
        return await answer(
            ctx,
            about(symbol, lambda conn, fn: render.walk_text(graph.walk(conn, fn, "callers", depth), limit)),
        )

    @server.tool()
    async def callees(symbol: str, depth: int = 3, limit: int = 40, ctx: Context | None = None) -> str:
        """Functions this one calls, directly or through other functions, up to `depth` steps."""
        return await answer(
            ctx,
            about(symbol, lambda conn, fn: render.walk_text(graph.walk(conn, fn, "callees", depth), limit)),
        )

    @server.tool()
    async def find(query: str, limit: int = 20, ctx: Context | None = None) -> str:
        """Find functions by exact name, part of a name, ns::Class::method, name@file, or file.c:LINE."""

        def body(conn: sqlite3.Connection) -> str:
            fns = symbols.find(conn, query) or symbols.search(conn, query, limit)
            if not fns:
                return f"No function matches '{query}'."
            return "\n".join(f"{f.qualified_name}@{f.file}  {f.where}  {f.signature}" for f in fns[:limit])

        return await answer(ctx, body)

    @server.tool()
    async def index_status(ctx: Context | None = None) -> str:
        """What AeroTrace indexed (files, functions, compile flags, libclang) and what it could not see."""

        def body(conn: sqlite3.Connection) -> str:
            meta = store.read_meta(conn)
            n = {
                t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("functions", "variables")
            }
            ptr = conn.execute("SELECT COUNT(*) FROM blind_spots WHERE kind = 'pointer_call'").fetchone()[0]
            flags = meta["compdb_path"] or "guessed (no compile_commands.json)"
            return "\n".join(
                [
                    f"Repo: {root.name}",
                    f"Indexed {meta['units']} source files: {n['functions']} functions, {n['variables']} variables.",
                    f"Compile flags: {flags}.",
                    f"Files with parse errors: {meta['files_with_errors']}.",
                    f"Call sites through pointers: {ptr} (matched by type where possible).",
                    f"libclang: {meta['libclang']}.",
                    *(f"Note: {n}" for n in graph.flag_limits(meta)),
                ]
            )

        # while a build runs, answer at once with its progress (agents poll this)
        return await answer(ctx, body, wait_if_busy=False)

    return server, state


def create_server(
    root: Path,
    build_dir: Path | None = None,
    *,
    jobs: int | None = None,
    wait: float = WAIT_SECONDS,
    build: BuildFn = build_index,
) -> MCPServer:
    """The MCP server for `root`. `jobs`: parallel parsers (default: CPU count). `wait` and `build` are
    for tests (how long a call waits for a build, and the build function)."""
    return _create(root, build_dir, jobs=jobs, wait=wait, build=build)[0]


def run(root: Path, build_dir: Path | None = None, jobs: int | None = None) -> None:
    logging.basicConfig(level=logging.WARNING, format="aerotrace: %(message)s")  # stderr only
    server, state = _create(root, build_dir, jobs=jobs)

    def quit_now(signum: int, _frame: object) -> None:
        # Exit at once. A normal unwind would wait for the thread blocked reading stdin, which only ends
        # when the client closes it, so Ctrl-C or SIGTERM would hang.
        state.shutdown(timeout=5)  # end a running build and its parser processes first
        try:  # what a normal exit does for multiprocessing: release the pool's semaphores
            import multiprocessing.util

            multiprocessing.util._exit_function()
        except Exception:
            pass
        os._exit(128 + signum)

    signal.signal(signal.SIGTERM, quit_now)
    signal.signal(signal.SIGINT, quit_now)
    try:
        server.run()  # returns when the client closes stdin
    finally:
        state.shutdown()  # end a running build and its parser processes, so we exit at once
