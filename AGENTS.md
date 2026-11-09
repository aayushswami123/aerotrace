# AGENTS.md

Rules for any AI coding agent working in this repo (Codex, Cursor, Copilot, Gemini CLI, Aider, ...).

**Read and follow [`CLAUDE.md`](CLAUDE.md).** It is the single source of truth for project rules, so the
rules live in one place. If anything here and `CLAUDE.md` disagree, `CLAUDE.md` wins.

The short version:

- Analysis is deterministic and fully local. No AI and no network inside `src/aerotrace/`.
- Never guess silently. Unknown links are blind spots or carry a confidence label.
- Work on a feature branch, use Conventional Commits, and run `ruff` and `pytest` before each commit.
- Never commit to `main`. Never add AI attribution to commits or PRs.
