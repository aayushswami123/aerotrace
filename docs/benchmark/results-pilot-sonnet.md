# Benchmark pilot: Sonnet, 4 tasks, 1 run per side

Status: **plan, written before any run.** Results are added below this section after the runs; this
section is not changed after the first run.

## What the owner decided (2026-11-09)

- Model: Sonnet. The harness passes the alias `sonnet` once (in `prepare`), reads the full model id that
  Claude Code resolves it to from the `system`/`init` event, and pins that id for every run.
- Tasks: R03, R04, B01 and one control from [tasks.md](tasks.md).
- One run per side ("with" AeroTrace, "without"). Same prompt and settings on both sides.
- "with": `aerotrace install --client claude` (MCP server in `.mcp.json` and the edit hook in
  `.claude/settings.json`), passed with `--mcp-config`. "without": no MCP server, no hook.
- Hard money limit: **$25** for all model spend, the `prepare` smoke run included.

## The control, chosen before any run

tasks.md has exactly one task called a control: **C02** ("C02 is a plain control: a text search finds
everything"). The hash order of protocol.md (`sha256("<task>/1")`) over the set {C02} picks C02.

For the record, a wider reading (C02 plus the tasks where we expect AeroTrace *not* to help, R06, R14,
R15, B02) would have picked R14 by the same hash order. We did not use that reading: those tasks are
blind-spot tasks, not controls, and B01 already covers a blind spot.

## Order and money

- Pairs run in the protocol's hash order `sha256("<task>/1")`: **R03, B01, R04, C02**. Both sides of a
  pair start at the same time.
- Per-run cap: `--max-budget-usd` = min(6, remaining / remaining runs), where remaining = $25 - spent -
  $0.50. The $0.50 is kept back because Claude Code stops only after the turn that passes the cap, so a run
  can go a little over.
- The harness starts no pair whose two caps would take the total past $25. Pairs it does not start are
  reported as "not run"; one side of a pair is never reported alone.
- Cost per run is `total_cost_usd` from the run's `result` event. A run with no `result` event (timeout,
  crash) is counted at its full cap.

## Where this differs from protocol.md (version 2)

The pilot is small on purpose. These differences are known and decided before the runs:

- 4 tasks, 1 run per side (protocol: 20 tasks, 3 runs per side). No percentages: case studies only.
- Model Sonnet instead of Opus. `--effort high` and every other flag as in the protocol.
- The check-validation gate (reference and wrong patches for every task) and the flake-rate runs are not
  done. Only the empty-diff check is redone (`prepare`: at least one check fails on the unchanged sources,
  for each of the 4 tasks). If a test fails when judging, the report says whether it also fails on the
  unchanged tree, but the verdict is not changed.
- Credentials: this machine uses AWS SSO. Its role credentials last one hour and renew by themselves while
  the SSO session lasts, so the 75-minute rule can't be applied as written. The harness instead checks
  that `aws configure export-credentials` works before every pair; the smoke run in `prepare` checks model
  access once.
- One pair at a time (2 agents at once), slots 0 and 1 (`BENCH_BASEPORT` 22000 and 23000).
- The agent's change is saved with `git add -A && git diff --binary --cached baseline`, so new files are
  in it too.
- The agent's environment is clean: only `HOME`, `USER`, `LOGNAME`, `LANG`, `PATH`, `SHELL`, `TERM`,
  the AWS/Bedrock variables, the `ANTHROPIC_DEFAULT_*_MODEL` / `ANTHROPIC_SMALL_FAST_MODEL` values,
  `DISABLE_AUTOUPDATER=1`, `CLAUDE_CONFIG_DIR` (a fresh copy of an empty seed folder), `TMPDIR` (inside the
  run folder) and the `BENCH_*` slot variables.

Harness: `uv run bench/run.py prepare|run|judge|report` ([bench/run.py](../../bench/run.py), tasks in
[bench/tasks.py](../../bench/tasks.py)). Raw logs: `bench/results/pilot-sonnet/`.
