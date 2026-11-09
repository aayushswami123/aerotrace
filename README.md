# AeroTrace

A safety layer for AI coding agents working on critical, legacy C and C++ code.
Before an agent edits code, AeroTrace shows what the change will affect. It runs fully on your machine.

Status: early development.

## Install

```bash
uv tool install git+https://github.com/aayushswami123/aerotrace   # or: pipx install git+...
```

## Use it with your coding agent

Run one command inside your C/C++ repo:

```bash
aerotrace install --client claude   # writes .mcp.json (Claude Code)
aerotrace install --client cursor   # writes .cursor/mcp.json (Cursor)
aerotrace install --client codex    # adds it to ~/.codex/config.toml (Codex, every repo)
```

Add `--dry-run` to see the change first. Only the `aerotrace` entry is added or replaced; other servers are kept.

The agent gets these tools: `impact`, `callers`, `callees`, `find` and `index_status`. The first question
builds the index. After that, files you edit are re-indexed before each answer.

## Use it from the terminal

```bash
aerotrace doctor               # checks libclang and finds compile_commands.json
aerotrace index                # builds .aerotrace/index.db
aerotrace impact setCommand    # what could break if this function changes
aerotrace callers dictAdd --depth 2
aerotrace find src/server.c:4221
```

For accurate results, give AeroTrace your build's `compile_commands.json`
(CMake: `-DCMAKE_EXPORT_COMPILE_COMMANDS=ON`; Make: run the build under [`bear`](https://github.com/rizsotto/Bear)).

## What the labels mean

Every link has a confidence label, and every impact answer ends with its limits.

| Label | Meaning |
|---|---|
| (none) | A direct call the compiler resolved |
| `virtual` | A virtual call that may land on this override |
| `via_pointer` | A call through a function pointer whose type matches |
| `ambiguous` | Several functions share this name; this is one of them |
| `data` | Reads a global or field the changed function writes |

Licensed under the Apache License 2.0. See [LICENSE](LICENSE).
