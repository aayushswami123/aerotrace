# Roadmap

Four phases. A phase ends only when its gate passes, measured by a command anyone can rerun. The lines
refer to [QUALITY_BAR.md](QUALITY_BAR.md). Work on a later phase does not start before the earlier gate
passes, unless the owner says so.

## Phase 1: Trust

Make the answers on the gate right before anything else.

- The compiler decides build and link breaks ([ADR 0009](decisions/0009-the-compiler-decides.md)): run a
  trusted compiler on the edited files in memory, in every captured build variant.
- Every UNKNOWN has a named reason and a fix. Remove the reasons that are really bugs.
- Every BREAKS carries the compiler's error text.
- Fix the known false-safe cases (issue #93) or prove they are out of reach and say so in the answer.

**Gate:** lines 1 to 4 of the bar on the gate: 0 false safe, a definite build answer for every reached
file, UNKNOWN under 2% with named reasons only, and every BREAKS with compiler text. `make gate` shows it.

## Phase 2: Proof

Show it on real code and real agents.

- The benchmark on Redis, SQLite and git, scored against the real build, with clangd and the other tools
  side by side.
- Hook speed on those repos, with checks that keep running past the budget.
- The agent pilot: agents with AeroTrace against agents with grep and a build.

**Gate:** lines 1 to 8 of the bar on the benchmark and in the pilot. Results are published with the
commands to rerun them.

## Phase 3: Safety gate

Make AeroTrace the check that runs before and after every AI edit.

- `verify`: after the edit, prove what happened (build result, the breaks that were predicted and the
  ones that were not).
- Receipts: a saved, deterministic record of each check and its proof.
- Track record: how often its answers were right on this repo, from the receipts.
- PR check: the same gate on every pull request, with no time limit in CI.

**Gate:** the bar still holds with the safety gate on, on the benchmark and on the gate.

## Phase 4: Scale and migrations

- Large repos and many build variants within the speed line.
- Migrations: big planned changes (API moves, compiler and standard upgrades) checked step by step.
- Ada, after C and C++ are solid.

**Gate:** the bar holds on the largest repos we support, and on at least one real migration.

## Paused

Dashboard and web UI work is paused until Phase 1 passes.
