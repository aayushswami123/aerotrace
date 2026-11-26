# The quality bar

This is what "better than anything available" means for AeroTrace. **Every prompt, PR and release is
judged against it.** A change that makes any line worse is not merged, whatever else it improves.

Words used below:

- **The gate**: `make gate` (`bench/gate/`), small cases whose truth comes from the real compiler and linker.
- **The benchmark**: `bench/cases/`, real changes on real repos (Redis, SQLite, git), scored the same way.
- **Reached file**: a file the change can affect that AeroTrace found (through includes, calls or data).

| # | Line | The bar | Measured by |
|---|---|---|---|
| 1 | False "safe" | **0** on the gate and the benchmark. Non-negotiable. | gate, benchmark |
| 2 | Build and link questions | A definite answer (BREAKS or builds) for **100%** of reached files, in every captured build variant. | gate, benchmark |
| 3 | "Not sure" (UNKNOWN) | Only for named reasons: callers in other repos, a build variant not captured, a time budget still running. **Under 2%** of gate cases. Each one says its reason and how to fix it. | gate (`UNKNOWN by reason`) |
| 4 | False alarms | **Fewer than clangd** on the benchmark. Every BREAKS carries the compiler's own error text. | benchmark |
| 5 | Exact cases | Per case, "all breaks found and zero false alarms" in **more cases than any other tool** on the benchmark, the real build included, while answering **before** the edit. | benchmark |
| 6 | Speed | Hook **p95 under 2 s** on Redis, SQLite and git. Checks keep running in the background past the budget. No time limit in CI. | gate (`Hook p50/p95`), benchmark |
| 7 | Answers | **10 lines or fewer** by default, the verdict first, one clear next step. | golden outputs |
| 8 | Agents | In the pilot, agents with AeroTrace leave **fewer broken builds AND use fewer tokens** than agents with grep and a build. | agent pilot |

## Rules for reading the bar

- **Line 1 beats everything.** If a fix for lines 2 to 8 adds one false "safe", it is rejected.
- **UNKNOWN is not a hiding place.** An UNKNOWN without a named reason counts as a failure of line 3.
  "Other" in the gate's reason table is a bug to fix, not a reason.
- **Numbers come from runs, not from claims.** Each line is shown by a command anyone can rerun
  (`make gate`, the benchmark scripts). Copying the tool's own output into a truth file is forbidden.
- **Never weaken a test or a case to pass a line.** A case the tool gets wrong stays wrong and is
  tracked (`bench/gate/known_limits.toml`, issues), not edited away.
- **The baseline only moves forward.** `make gate` fails if anything is worse than `bench/gate/BASELINE.md`.
  A new baseline is recorded on purpose, in a PR.

## Where we are

`bench/gate/BASELINE.md` has the current gate numbers. The phases and their gates are in
[ROADMAP.md](ROADMAP.md).
