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
- **check-ui**: polish of the `aerotrace check` text output.
- **owner**: Aayush. Not an agent.

| Branch | Owner | What it is for | Area | State |
|---|---|---|---|---|
| `docs/dashboard-plan` (#62) | product | Dashboard and benchmark plan, design system, web UI | `docs/design/`, `docs/decisions/`, `docs/plans/dashboard*`, `src/aerotrace/ui/web/`, `tests/ui/`, `CLAUDE.md` | in progress |
| `bench/cases` (#63) | product | 30 benchmark cases | `bench/cases/` | in progress |
| `bench/pilot-sonnet` | product | Harness and plan for the $25 Sonnet benchmark pilot | `bench/` | waiting to run |
| `docs/launch-readme` (#54) | product | Launch README | `README.md`, `docs/launch/` | waits for benchmark results; do not merge |
| `feat/check-output-polish` (#61) | check-ui | Headline counts, build coverage line, `--all`, wrapping for `check` | `src/aerotrace/ui/terminal.py`, `ui/html.py`, `ui/schema.py`, `analysis/check.py`, `analysis/graph.py`, `cli.py` | review fixes pushed; waiting for CI |
| `fix/data-gaps` (#56) | merge | Data gaps fix | `src/aerotrace/frontends/cpp/parse.py` | reviewed, CI green; conflicts with `launch-candidate` in `parse.py` and `CHANGELOG.md` (owner to OK the fix) |
| `chore/setup-uv-7` (#64) | merge | CI: setup-uv 6 to 7 (replaces Dependabot's #6) | `.github/workflows/ci.yml` | waiting for CI |
| `feat/test-impact-coverage` | owner | gcov coverage for test impact | `src/aerotrace/analysis/testimpact.py` and more | parked until after launch |
| `docs/morning-report` | owner | Day notes and screenshots | `docs/launch/` | owner decides (has budget figures) |
