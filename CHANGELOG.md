# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- MCP server (`aerotrace mcp`) with `impact`, `callers`, `callees`, `find` and `index_status` tools.
  It builds the index on the first question and re-indexes changed files before each answer.
- `aerotrace install --client claude|cursor|codex` adds the server to Claude Code (`.mcp.json`),
  Cursor (`.cursor/mcp.json`) or Codex (`~/.codex/config.toml`, or `$CODEX_HOME`), keeping other settings.

- `aerotrace callers`, `callees` and `impact` (blast radius): transitive, with a depth limit, nearest first,
  capped output, and a confidence label on every result. Impact also lists functions that read data the
  changed function writes, and ends with the limits of the answer.
- `aerotrace find`: by name, `ns::Class::method`, `name@file` or `file.c:LINE`. A name that matches several
  functions returns all of them; we never pick one silently.
- Function-pointer pass: a call through a pointer of type T is linked (`via_pointer`) to every function of
  type T whose address is taken. On Redis this links `call()` to all 323 command handlers.

- `aerotrace index`: parses C and C++ with libclang and stores calls, overrides, reads and writes of
  globals, struct fields and static locals in `.aerotrace/index.db`.
- Uses the project's `compile_commands.json` (repo root, `build*/`, `cmake-build-*/`, `out/*/` or `--build-dir`).
  Without one, it guesses flags, parsing C as C and C++ as C++.
- Collision-safe IDs: two `main()`s, or two `static` functions in two files with the same name, stay apart.
  References a file can't resolve to one definition are marked `ambiguous`.
- Blind spots are recorded: calls through pointers, and calls to functions with no parsed definition.
- Functions whose address is taken (callback tables) are recorded, for the function-pointer pass.
- `aerotrace doctor` (libclang, flags, headers) and `aerotrace status` (counts, files changed since indexing).
- The system libclang is preferred over the bundled one; its version is part of the cache key.
- Flags that would load compiler plugins are removed from compile commands.
- Repo rules, contribution guide, CI, templates and the first decision records.
