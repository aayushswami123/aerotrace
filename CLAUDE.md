# AeroTrace — rules for AI coding agents

This file is for Claude Code. `AGENTS.md` points here, so every agent (Codex, Cursor, ...) follows the same rules.
Human contributors: read `CONTRIBUTING.md`. It has the same rules plus the workflow.

## What this project is

AeroTrace is the change-safety gate for AI-written C/C++ code. Before an edit it says exactly what will
break; after the edit it proves what happened. The compiler and linker decide build and link breaks;
AeroTrace adds what they can't see (function pointers, data flow, behaviour changes, tests, history).
It ships as an MCP server, a hook and a command-line tool. Everything runs on the user's machine.

Read these first: `docs/PRODUCT.md` (what it is), `docs/QUALITY_BAR.md` (every prompt and PR is judged
against it), `docs/ROADMAP.md` (phases and their gates), `docs/decisions/` (ADRs).

## Product rules (never break these)

1. **Deterministic.** The same code, flags and tool version must give the same answer. No randomness.
   Sort every output. No timestamps or machine paths inside answers.
2. **Fully local.** No network calls at analysis time. No telemetry. Code never leaves the machine.
3. **No AI inside the analysis core.** No LLM calls, embeddings or ML models anywhere in `src/aerotrace/`.
4. **Never guess silently.** If a link can't be proven, either leave it out and count it as a blind spot,
   or add it with a confidence label (`exact`, `via_pointer`, `ambiguous`, ...). Every answer must say
   what it could not see.
5. **A wrong "safe" answer is the worst bug.** Missing a caller is worse than listing an extra one.
   When in doubt, report the uncertainty.
6. **Never write into the user's source tree** except `.aerotrace/` (the index) and files the user asked
   `aerotrace install` to change. Edits are applied in memory only. **Never run build scripts** (`make`,
   `cmake`, `ninja`, configure, anything from the repo). The only programs we may run are a *trusted*
   compiler and linker, on the edited code in a temp folder (ADR 0009). Today the code still runs none.
7. **Out of scope for now:** languages other than C/C++ (Ada comes later), cloud or team features.
   **Dashboard and web UI work is paused until Phase 1 (Trust) passes** (`docs/ROADMAP.md`).

## Code layout

- `src/aerotrace/index/store.py`: the one graph schema (SQLite tables, `SCHEMA_VERSION`). Changing it is a
  big decision: ask first, bump the version, and write an ADR in `docs/decisions/`.
- `src/aerotrace/model/`: shared types used across layers (kept small).
- `src/aerotrace/toolchain/`: finding libclang, reading `compile_commands.json`, unified diffs, and git
  (read-only).
- `src/aerotrace/frontends/cpp/`: the libclang parser. It turns code into facts and nothing else.
- `src/aerotrace/index/`: building, caching and storing the index (SQLite in `.aerotrace/`).
- `src/aerotrace/analysis/`: questions over the index (callers, impact, diff, data, tests, rules).
- `src/aerotrace/mcp/`, `cli.py`, `hooks/`: thin layers. **No analysis logic here.** They call `analysis/`.
- `tests/fixtures/`: tiny C/C++ programs where we know the right answer. `tests/golden/`: expected outputs.

## Engineering rules

- Python 3.11+. Type hints on all public functions. `ruff check` and `ruff format` must pass.
- Standard library first. A new dependency needs a reason in the PR description.
- Every bug fix in the parser or analysis comes with a fixture that reproduces it.
- Every new edge kind or analysis needs a fixture test showing a correct answer **and** a blind spot.
- Tests must not need the network. Real-repo tests (Redis, SQLite) are marked `@pytest.mark.realrepo`
  and are skipped by default; CI runs them on every PR.
- Every new analysis feature adds golden facts in `tests/realrepo/` (see its README): hand-checked, with
  the source lines that prove them, and at least one "must not" claim. Never copy the tool's output into
  a fact. A fact the tool gets wrong is marked `known_bug`, not weakened.
- Output for agents must be small. Rank results and cap lists, and say how many were left out.
- Keep messages, docs and tool descriptions in plain, short English.

## Testing rules

- **Failing test first.** Every fix starts with a test or gate case that fails, then the fix makes it pass.
- **Never weaken a test**, a golden file, a realrepo fact or a gate case to make a change pass. A wrong
  answer is marked (`known_bug`, `bench/gate/known_limits.toml`, an issue), never edited away.
- If a change can move a BREAKS, UNKNOWN or "nothing found" answer or an exit code, run `make gate`
  (macOS with Apple clang). It fails if anything is worse than `bench/gate/BASELINE.md`. A new baseline
  is recorded on purpose, in a PR (`make gate-baseline`).

## Git workflow

- Before working on a branch or file area, check `docs/COORDINATION.md`. Add your branch when you start it;
  remove it when it's merged.
- Never commit to `main` or `launch-candidate`. Commit only on feature branches: `feat/<topic>`,
  `fix/<topic>`, `chore/<topic>`, `docs/<topic>`, each in its own git worktree.
- **Small PRs into `launch-candidate`.** One topic per PR.
- Commit after each meaningful, working step. Conventional Commits: `feat(mcp): add install command`.
- Before every commit run `uv run ruff check . && uv run ruff format --check . && uv run pytest`.
- Open a PR with the template filled in. Don't merge your own PR without the owner's OK.
- No AI attribution in commits or PRs (no `Co-Authored-By` lines for tools, no "Generated with" footers).

## Agents and cost

Agents live in `.claude/agents/`. Pick the cheapest one that can do the job. **Nobody reviews their own work.**

| Agent | Model | Job |
|---|---|---|
| `scout` | Haiku | Runs and reads: tests, `make gate`, logs, numbers. Never edits. |
| `builder` | Sonnet | Writes most code, tests and docs. |
| `architect` | Opus | Designs only (plans, ADRs, test plans). No product code. |
| `deep-fixer` | Opus | Only for the hard parts a prompt names. |
| `reviewer` | Sonnet | Reviews normal PRs. |
| `critical-reviewer` | Opus | Final review only, for PRs that change BREAKS, UNKNOWN, "nothing found" or exit codes. |

Cost rules:

- The orchestrator (the main session) reads summaries only. It hands file reading and long outputs to
  subagents instead of reading them itself.
- Subagents report in 10 lines or fewer.
- Report the session cost (`/cost`) at the end of each task.
- **Stop and ask the owner if a session passes $20.**

## Commands

```bash
uv sync                      # install
uv run pytest                # tests
uv run pytest -m realrepo    # slow tests on real repos (needs network the first time)
uv run ruff check . && uv run ruff format .
uv run aerotrace --help
make gate                    # gate cases vs BASELINE.md (macOS, Apple clang)
```
