# 0008: A local, offline, read-only UI

- Status: accepted
- Date: 2026-11-24

## Context

Product rule 7 said "no dashboards or web UI". Since then the owner asked for `aerotrace check --html` (one
static offline file) and now for `aerotrace ui`: a local dashboard for exploring checks, their impact
graph and the benchmark. A graph of thousands of nodes is hard to read in a terminal, and the launch
needs screenshots that show real results.

The risks of any UI here: it could send code off the machine, load things from the internet, change the
user's code, or show numbers that analysis didn't produce.

## Decision

Rule 7 becomes: **only a local, offline, read-only UI.**

- It runs on the user's machine. `aerotrace ui` serves on `127.0.0.1` only, with a random per-run token
  and a Host header check.
- Offline: every font, script and image is bundled in the package. No CDN, no remote fonts, no
  telemetry. A Content-Security-Policy blocks anything else.
- Read-only: only `GET`. It reads `.aerotrace/` and, for code snippets, files listed in the index. It
  never writes outside `.aerotrace/`, never runs `aerotrace check` or the user's build.
- No analysis in the UI. It shows what `analysis/` produced and decides nothing.
- Hosted or cloud UIs, accounts and team features stay out of scope.

The plan is `docs/plans/dashboard-and-benchmark.md`. The design system is `docs/design/`.

## Consequences

- Easier: big results are readable, and the launch can show real screenshots.
- Harder: we now ship and test frontend code (browser and screenshot tests, accessibility, a localhost
  server's security).
- Watch: the package size (fonts and JS, target under 600 KB), that the UI never shows a number analysis
  didn't produce, and that no request ever leaves `127.0.0.1` (a test blocks sockets and checks it).
