# Plan: a faster `check`

Status: plan only, not built. Measured on an Apple M1 Pro after benchmark run 1.

## What a check spends its time on

Measured case: Redis 8.10.3, a patch that adds a parameter to `parseScanCursorOrReply` (`src/db.c` and
`src/server.h`). Wall time 2.6 s (three runs: 2.65, 2.59, 2.67). Benchmark run 1: median 1.0 s, range
0.3 to 6.3 s (the slow end is SQLite, one 9 MB file parsed twice).

`python -m cProfile` (4.7 s under the profiler):

| part | time | why |
|---|---|---|
| outlines of the changed files (4: old and new `db.c`, old and new `server.h`) | 3.2 s | |
| of which: walking the syntax tree in Python (`visit`, `_outline_function`) | 2.9 s | 12,230 functions visited, most of them from included headers, not from the changed files |
| of which: libclang parsing itself | about 0.3 s | |
| loading compile flags (`load_compdb`) | 0.7 s | `guess_units` scans the repo for every C/C++ file (0.45 s) and `rel_to` resolves 3,744 paths (0.44 s, overlapping) |
| everything else (diff, graph walk, answer) | under 0.5 s | |

So the time is mostly Python, not libclang.

## The top two speedups

1. **Outline only the file asked for.** `function_outline` walks every function in the translation unit,
   including every header it includes, then keeps only the ones in the target file. It should skip a
   cursor from another file before going into it. For other files it only needs prototypes (name and
   signature, for the "declared elsewhere" check), which are cheap to read without the full outline.
   Expected: most of the 2.9 s, so a typical check goes from about 2.6 s to about 1 s.
   Risk: the prototype and `noproto` logic reads other files; the golden facts and `test_check.py`
   cover it.
2. **Load compile flags without scanning the repo.** The Outliner calls `find_units`, which also runs
   `guess_units` to list the sources that are not in `compile_commands.json`. Outlines don't need that
   list. Load only the database (cached by its hash, which the index already stores), and cache
   `realpath` lookups. Expected: about 0.7 s per check, and the same for every Claude Code hook call.

## Later

- Parse the old and new versions of a file at the same time, in two child processes.
- Reuse one parse for a header and a `.c` file of the same change that includes it.

## How we will measure

The same patch, 5 runs, median, before and after each step. Benchmark run 2 reports the per-case times.
The answers must not change: the golden facts and the UI goldens must pass unchanged.
