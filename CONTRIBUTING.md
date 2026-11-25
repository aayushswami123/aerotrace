# Contributing to AeroTrace

Thanks for helping. This page covers how we work. The product and code rules are in [`CLAUDE.md`](CLAUDE.md);
they apply to people and AI agents alike.

## Set up

You need Python 3.11+, [uv](https://docs.astral.sh/uv/), git, and a C/C++ compiler (clang or gcc).

```bash
uv sync
uv run pytest
```

AeroTrace uses the libclang on your machine when it can find one (Xcode or an LLVM install). If it can't,
it falls back to the libclang bundled with the `libclang` Python package. Set `AEROTRACE_LIBCLANG` to a
`libclang.dylib` / `libclang.so` path to choose one yourself. `aerotrace doctor` shows which one is used.

## Branches

- `main` always works and is always releasable. Nobody pushes to it directly.
- One branch per change, cut from `main` (or from the branch it builds on, see "Stacked PRs"):
  `feat/<topic>`, `fix/<topic>`, `chore/<topic>`, `docs/<topic>`, `test/<topic>`, `refactor/<topic>`.
- Keep branches short-lived. Rebase on `main` if it has moved.
- Before working on a branch or file area, check `docs/COORDINATION.md`. Add your branch when you start it;
  remove it when it's merged.

## Commits

We use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <what changed, in the imperative>

<why, if it isn't obvious>
```

Types: `feat`, `fix`, `perf`, `refactor`, `test`, `docs`, `chore`, `ci`, `build`.
Scopes: `model`, `toolchain`, `cpp`, `index`, `analysis`, `mcp`, `cli`, `hooks`, `bench`, `repo`.

- One logical change per commit. Each commit should pass the tests.
- Breaking change: add `!` after the type (`feat(model)!: ...`) and explain it in the body.
- No AI attribution lines in commits or PRs.

## Before you commit

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run pytest -m realrepo -rxX   # golden facts on pinned Redis and SQLite (downloads once, ~20 s)
```

If your change touches analysis, add golden facts for it in `tests/realrepo/`. Its README explains how to
check a fact by hand and write down the source lines that prove it.

Or install the hooks once with `uv run pre-commit install` and they run on every commit.

## Pull requests

- Fill in the PR template: what, why, how you tested it, and the accuracy impact.
- Keep PRs small. One build item or one fix per PR.
- CI must be green: `lint` and `test`.
- Resolve every review thread before merging.
- We use **merge commits** (not squash) so stacked PRs keep working. The branch is deleted on merge.

### Stacked PRs

When feature B needs feature A, branch B off A and open B's PR against A's branch. When A merges,
GitHub moves B's PR onto `main` for you.

## Accuracy rules

AeroTrace is a safety tool. A wrong answer that looks safe is the worst bug we can ship.

- Every parser or analysis fix comes with a fixture in `tests/fixtures/` that failed before the fix.
- A new kind of link needs a test for a correct case **and** a blind-spot case.
- If you change any answer in `tests/golden/`, explain why in the PR. Never just regenerate the goldens.
- Wrong-answer reports use the "Wrong analysis result" issue template and get top priority.

## Decisions

Big decisions (schema, dependencies, storage format, public tool names) get a short Architecture
Decision Record in `docs/decisions/NNNN-title.md`. Copy `0000-template.md`.

## Releases

Versions follow [SemVer](https://semver.org/). Every user-visible change goes under "Unreleased" in
[`CHANGELOG.md`](CHANGELOG.md) in the same PR.
