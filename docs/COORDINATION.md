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
- **owner**: Aayush. Not an agent.

| Branch | Owner | What it is for | Area | State |
|---|---|---|---|---|
| `docs/dashboard-plan` (#62) | product | Dashboard and benchmark plan, design system, web UI | `docs/design/`, `docs/decisions/`, `docs/plans/dashboard*`, `src/aerotrace/ui/web/`, `tests/ui/`, `CLAUDE.md` | in progress |
| `bench/truth` (#74) | product | Benchmark truth (build each case), tool runs, scores, `docs/benchmark/RESULTS.md` | `bench/cases/patches/`, `bench/truth/`, `bench/results/`, `docs/benchmark/` | review |
| `feat/bench-page` (#76) | product | Benchmark results page `docs/benchmark/index.html`, generated from the run | `src/aerotrace/ui/bench_page.py`, `src/aerotrace/ui/web/bench-page.*`, `bench/page/`, `docs/benchmark/index.html`, `docs/benchmark/screenshots/` | review (on top of #62 and #74) |
| `feat/ui-dashboard` (#79) | product | `aerotrace ui` dashboard, its tests, CI browser job, launch screenshots | `src/aerotrace/ui/api.py`, `ui/server.py`, `ui/web/app.*`, `ui/web/graph.js`, `cli.py` (the `ui` command only), `tests/ui/`, `tests/golden/ui/`, `bench/ui/`, `docs/design/dashboard/`, `docs/launch/screenshots/`, `.github/workflows/ci.yml` (browser job) | review (on top of #76 and #80) |
| `feat/save-checks` (#80) | product | `aerotrace check` keeps each answer in `.aerotrace/checks/` (`--no-save`) | `src/aerotrace/ui/saved.py`, `cli.py` (check save only), `tests/test_save_checks.py` | review |
| `bench/pilot-sonnet` | product | Harness and plan for the $25 Sonnet benchmark pilot | `bench/` | waiting to run |
| `docs/launch-readme` (#54) | product | Launch README | `README.md`, `docs/launch/` | waits for benchmark results; do not merge |
| `fix/wrong-safe-answers` | merge | The 4 wrong "safe" answers from benchmark run 1 (redis-05, curl-05, redis-07, curl-07) | `src/aerotrace/analysis/check.py`, `analysis/data.py`, `index/history.py`, `ui/terminal.py` | review |
| `feat/test-impact-coverage` | owner | gcov coverage for test impact | `src/aerotrace/analysis/testimpact.py` and more | parked until after launch |
| `docs/morning-report` | owner | Day notes and screenshots | `docs/launch/` | owner decides (has budget figures) |
