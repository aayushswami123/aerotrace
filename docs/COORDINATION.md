# Who is working on what

Several agent sessions work on this repo at the same time. Before you start on a branch or a file area,
read this table. If someone else owns it, don't touch it: ask the owner (Aayush) first.

- Add your branch here when you start it. Remove it when it is merged (or dropped).
- Edit this file on `launch-candidate` (through a small PR, or in the branch the coordinator is merging
  next), so every session sees the same table.
- "Area" is the files the branch changes, so others can avoid them.
- Keep it short. One row per branch.

Sessions:
- **merge**: the merge coordinator. Merges finished, reviewed work into `launch-candidate`. Builds nothing.
- **product**: the dashboard, benchmark and UI session.
- **audit-fix**: the session fixing docs/audit must-fix items.
- **owner**: Aayush. Not an agent.

Dashboard work (#79, #80) is paused until the audit must-fix list is done (owner's call, 2026-11-24).

| Branch | Owner | What it is for | Area | State |
|---|---|---|---|---|
| `docs/dashboard-plan` (#62) | product | Dashboard and benchmark plan, design system, web UI | `docs/design/`, `docs/decisions/`, `docs/plans/dashboard*`, `src/aerotrace/ui/web/`, `tests/ui/`, `CLAUDE.md` | in progress |
| `bench/cases` (#63) | product | 30 benchmark cases | `bench/cases/` | in progress |
| `bench/pilot-sonnet` | product | Harness and plan for the $25 Sonnet benchmark pilot | `bench/` | waiting to run |
| `docs/launch-readme` (#54) | product | Launch README | `README.md`, `docs/launch/` | waits for benchmark results; do not merge |
| `fix/wrong-safe-answers` | merge | The 4 wrong "safe" answers from benchmark run 1 (redis-05, curl-05, redis-07, curl-07) | `src/aerotrace/analysis/check.py`, `analysis/data.py`, `index/history.py`, `ui/terminal.py` | review |
| `fix/git-no-lazy-fetch` | audit-fix | A-07: partial clones never fetch; a missing object is reported | `src/aerotrace/toolchain/git.py`, `tests/test_git_partial_clone.py` | in review |
| `feat/test-impact-coverage` | owner | gcov coverage for test impact | `src/aerotrace/analysis/testimpact.py` and more | parked until after launch |
| `docs/morning-report` | owner | Day notes and screenshots | `docs/launch/` | owner decides (has budget figures) |
| `fix/removed-fn-diff` | audit-fix | A-01, A-02: removed functions seen as "changed" | `src/aerotrace/analysis/diff.py`, tests | in review |
| `fix/check-dupes-self-edits` | audit-fix | A-05 duplicate definitions, A-12 false alarms | `src/aerotrace/analysis/check.py`, tests | in review |
| `fix/unknown-headline` | audit-fix | A-04 headline: UNKNOWN when any part was not checked | `analysis/check.py`, `ui/terminal.py`, tests | in review (stacked on #81 #82 #83) |
