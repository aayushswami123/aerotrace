<!--
Draft README sections for the launch (work item 9). Not README.md yet.
- {{BENCH_HEADLINE}} is filled in from work item 8, word for word. Do not edit it by hand.
- The demo video and screenshots come later, from a real Redis change.
- `aerotrace check` is built in item 5 Part C. Before launch, check every command below against the real CLI.
-->

# AeroTrace

**Before an AI agent edits your C or C++ code, AeroTrace tells it what the change will break. It runs on
your machine, and its answers come from the compiler, not a guess.**

<!-- DEMO VIDEO: docs/launch/demo.mp4 (about 60 seconds: an agent edits a Redis function, `aerotrace check`
     shows what it would break, the agent fixes the callers). Placeholder until recorded. -->

{{BENCH_HEADLINE}}

## Install

```bash
uv tool install git+https://github.com/aayushswami123/aerotrace
```

## How it works

1. **Index.** AeroTrace reads your build's `compile_commands.json` and parses every file with libclang,
   using your real compile flags. It stores who calls what, and which globals and struct fields each
   function reads and writes, in `.aerotrace/` inside the repo.
2. **Map the change.** It maps a diff (or an edit an agent is about to make) to the functions, globals and
   fields it touches, including functions the change deletes.
3. **Answer.** It follows callers and data uses outward, and answers with what breaks, what may be affected,
   and how sure each link is (`exact`, `probable` or `unknown`). Every answer also lists what it could not
   see.

```bash
aerotrace check                     # what your uncommitted change can break
aerotrace check --html report.html  # the same answer as one offline HTML file you can click through
```

## Use it with your coding agent

Run one command inside your C/C++ repo:

| Assistant   | Command                                | What it changes                                                        |
|-------------|----------------------------------------|------------------------------------------------------------------------|
| Claude Code | `aerotrace install --client claude`    | `.mcp.json` in the repo                                                |
| Cursor      | `aerotrace install --client cursor`    | `.cursor/mcp.json` in the repo                                         |
| Codex       | `aerotrace install --client codex`     | `~/.codex/config.toml` (or `$CODEX_HOME/config.toml`), for every repo  |

Add `--dry-run` to see the change first. Only the `aerotrace` entry is added or replaced; other servers
are kept.

## Honest limits

- **C and C++ only.** Other languages are not read.
- **It needs your build's `compile_commands.json`** for accurate results. Files the build does not list
  are not indexed, and the answer says so.
- **Macros and type changes are not followed.** A changed macro or `typedef` is listed under "changes
  AeroTrace could not follow", never dropped.
- **Function pointers are matched by type.** A call through a pointer is linked to every function of
  that type and labelled `probable`.
- **Code it cannot see is unknown, not safe.** Data passed to a library or through `void *` is labelled
  `unknown`. "Nothing found" always comes with the list of what was not checked.
- **It does not run your build or your tests.** When it knows which tests reach the change, it lists them; you run them.
- Tested on Linux and macOS.

Nothing leaves your machine: no network calls during analysis, no telemetry, and the HTML report loads
nothing from the internet.

## License

Apache-2.0. See [LICENSE](LICENSE).
