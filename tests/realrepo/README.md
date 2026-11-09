# Golden facts on real repos

A safety net: hand-checked facts about real code that AeroTrace must keep getting right.

```bash
uv run pytest -m realrepo -rxX     # downloads the pinned sources once (~7 MB), then about 20 seconds
```

CI runs this on every PR, on Linux and macOS.

## The pinned repos

| Repo | Version | Source | Checked by |
|---|---|---|---|
| Redis | 8.10.3 | `download.redis.io` release tarball | SHA-256 from `redis/redis-hashes` |
| SQLite | 3.54.1 | official amalgamation (`sqlite3.c`, `shell.c`) | SHA3-256 from sqlite.org, plus our SHA-256 |

`repos.py` pins them. Archives are cached in `~/.cache/aerotrace-realrepo` (or `$AEROTRACE_REALREPO_CACHE`)
and unpacked fresh for every run. We never run a repo's build. The compile flags are written in
`repos.py`, copied from each project's own build files (Redis: `src/Makefile` with `MALLOC=libc`).

## What a fact is

Each fact in `facts_redis.py` and `facts_sqlite.py` has:

- **why**: what is true, in plain words.
- **proof**: the exact source lines (file, line, text) that show it. Open them and you can check the fact
  yourself, without trusting AeroTrace.
- **must**: what AeroTrace has to report (a caller, a callee, a label, a written field, ...).
- **must_not**: what AeroTrace must never claim. False alarms matter as much as misses.

Two tests run per fact:

1. `test_proof` checks that every proof line really is in the pinned source. If it isn't, the *fact* is
   wrong, not the tool.
2. `test_fact` asks AeroTrace the same way the CLI and MCP server do, and compares.

Expected answers never come from the tool. Long lists come from the source text itself: for example, the
307 command handlers are parsed out of Redis's `commands.def`.

## Facts the tool gets wrong today

A fact with `known_bug` still runs, and is expected to fail (pytest "xfail", strict). If a change fixes
the bug, the test shows **XPASS** and fails, so you remove `known_bug` and the fix is counted. If a change
breaks a fact that passed, CI is red.

## Adding a fact

1. Find the claim in the source. Read the function it sits in, and check the `#if` blocks around it (is
   that code compiled in this build?). Use grep when the fact says "exactly".
2. Write the proof lines. Copy the text from the file, don't retype it.
3. Run the suite. If AeroTrace disagrees, go back to the source before you decide who is wrong. In the
   first draft of this suite, 5 of 8 disagreements were mistakes in the facts, not in the tool.
4. If the tool is wrong, open a GitHub issue and add `known_bug="#<issue>: ..."`. Don't weaken the fact.
