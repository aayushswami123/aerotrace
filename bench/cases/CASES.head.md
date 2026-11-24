# Benchmark cases (Phase 1, step 1)

Status: **drawn, not run.** No tool (AeroTrace, clangd, cscope, ripgrep) has been run on any case. This
list is committed first so it can't be tuned to the results. The owner approves it before anything runs.

Plan: `docs/plans/dashboard-and-benchmark.md` section 7 (PR #62).

## What a case is

A small, realistic edit to real code in a pinned release: Redis 8.10.3, SQLite 3.54.1 (amalgamation) and
curl 8.23.0. The edits are **made by us**, not taken from the projects' history. Each kind of edit is
one an agent really makes (change a signature, remove a function, rename a field...), applied to a
symbol drawn by a fixed seed.

- curl 8.23.0: `https://curl.se/download/curl-8.23.0.tar.gz`, sha256
  `e3de77cfa6367083c776a26647fc9f633b538c06c3baa73d931a252ac8f72b49`. curl.se publishes PGP signatures,
  not hashes. gpg isn't installed here, so the hash was checked against the `digest` of the same asset
  on the GitHub release `curl-8_23_0` (they match).
- Redis and SQLite: as pinned in `tests/realrepo/repos.py`.

## How the cases were drawn (`bench/cases/pick.py`)

1. The 10 kinds of change are fixed for every repo (table at the end): 3 easy, 6 hard, 1 "might break".
2. For each kind, candidates come from plain regex scans of the source (`bench/accuracy/cfuncs.py`).
   AeroTrace is never used to find or filter them.
3. Candidates are ordered by `sha256("aerotrace-bench-cases-1/" + repo + "/" + kind + "/" + id)`. The
   first one that passes the kind's filter is taken. A name is used by at most one case per repo.
4. Filters: at most 40 mentions (so the truth can be checked by hand), and each kind must mean what it
   says. "Direct calls only" kinds have 2 to 30 calls and no use as a value. Pointer kinds are used as a
   value at least once (`ptr_remove`: never called by name). `int_to_void` returns `int`. `field_type`
   is a plain integer.
5. Redis and SQLite definitions must be compiled in the default build (`cc -E`, `bench/accuracy/live.py`).
   This was checked on the benchmark machine (Apple M1 Pro, arm64), so arm64-only code can be drawn
   (redis-02 is). The benchmark runs on this machine only.

Reproduce: unpack the three pinned archives into empty folders, then
`python3 -I bench/cases/pick.py <redis> <sqlite> <curl> > bench/cases/cases.json` and
`python3 -I bench/cases/render.py > bench/cases/CASES.md`. A second run gave a byte-identical
`cases.json` (about 2 minutes, most of it `cc -E`).

## Rules fixed now, before the run

- **curl compiled check.** curl's default build needs its `./configure`, run in a temp copy (never a
  user's tree). A curl case whose code is not compiled after configure is replaced by the next candidate
  in seed order.
- **Live use.** For fields, globals and macros, at least one use must be in compiled code. If not, the case is
  replaced the same way. (For example curl-06 and curl-08 may sit behind build options.)
- **Macro variants.** When a macro has several `#define`s under `#if`, the edit is made to all of them.
- **Edit can't be applied.** If the edit is impossible as written (for example the field is a bitfield
  that can't become a pointer), the case is replaced the same way.
- Every replacement is logged in `cases.json` with its reason, and shown in the results.

## Known slant, said up front

- `macro_arity` is a common gap for tools that read the syntax tree after the preprocessor has run, and
  `remove_fn` only fails at link time. How AeroTrace does on them hasn't been checked; they stay in
  either way.
- The filter "at most 40 mentions" favours less-used symbols. Large, heavily used symbols are not tested.

## Cases
