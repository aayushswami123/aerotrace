# AeroTrace — rules for AI coding agents

This file is for Claude Code. `AGENTS.md` points here, so every agent (Codex, Cursor, ...) follows the same rules.
Human contributors: read `CONTRIBUTING.md`. It has the same rules plus the workflow.

## What this project is

AeroTrace tells an AI coding agent what a change to C or C++ code will affect *before* the edit:
callers, callees, transitive impact, and the globals and struct fields the code reads and writes.
It ships as an MCP server and a command-line tool. Everything runs on the user's machine.

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
   `aerotrace install` to change. Never run the user's build (`cmake`, `make`) on our own.
7. **Out of scope for now:** dashboards or web UI, languages other than C/C++ (Ada comes later),
   cloud or team features.

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

## Git workflow

- Never commit to `main`. One branch per feature: `feat/<topic>`, `fix/<topic>`, `chore/<topic>`, `docs/<topic>`.
- Commit after each meaningful, working step. Conventional Commits: `feat(mcp): add install command`.
- Before every commit run `uv run ruff check . && uv run ruff format --check . && uv run pytest`.
- Open a PR with the template filled in. Don't merge your own PR without the owner's OK.
- No AI attribution in commits or PRs (no `Co-Authored-By` lines for tools, no "Generated with" footers).

## Commands

```bash
uv sync                      # install
uv run pytest                # tests
uv run pytest -m realrepo    # slow tests on real repos (needs network the first time)
uv run ruff check . && uv run ruff format .
uv run aerotrace --help
```
