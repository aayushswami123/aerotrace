# 0003: Store the index in `.aerotrace/` as SQLite

- Status: accepted
- Date: 2026-11-08

## Context

The MCP server, the CLI and the Claude Code hook all need the same index. Agents start the MCP server
from the project folder.

## Decision

Keep the index in `<repo>/.aerotrace/index.db` (SQLite, standard library only). On creation, write
`.aerotrace/.gitignore` containing `*`, so the index is never committed and we don't edit the user's own
`.gitignore`. Store one row per file with its content hash, so incremental updates are possible later.

## Consequences

- Nothing to install or run besides AeroTrace itself.
- Each clone or worktree has its own index. That's correct, since each may be on a different commit.
