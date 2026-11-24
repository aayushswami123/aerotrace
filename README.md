# AeroTrace

**Before an AI agent edits your C or C++ code, AeroTrace can tell it what the change can break. It runs on
your machine, and it says how sure it is about every link.**

<!-- DEMO VIDEO: docs/launch/demo.mp4 (about 60 seconds: an agent edits a Redis function, `aerotrace check`
     shows what it would break, the agent fixes the callers). Placeholder until recorded. -->

{{BENCH_HEADLINE}}

Status: early development.

## Honest limits

Read these first. AeroTrace is a static tool, and these are the places where it can be wrong or blind:

- **One build configuration.** It sees the code your `compile_commands.json` compiles, with those flags.
  Code under other `#ifdef` settings, and files the build does not list, are not analysed (the answer says
  how many and where).
- **Macros and types are not followed.** A changed macro or `typedef` is listed under "changes AeroTrace
  could not follow", never dropped.
- **C++ templates are only partly followed.** Uses through a type that depends on a template parameter
  are not linked.
- **Tests written as scripts are not seen.** Redis's and SQLite's Tcl tests, Python or shell tests: a static
  link from a C/C++ test is all it can find. Code no C/C++ test reaches is "not known", never "untested".
- **Many links are `probable`, not `exact`.** Calls through function pointers (matched by type), virtual
  calls and functions that share a name can't be resolved by the compiler alone, so AeroTrace lists them
  and labels them.
- **"Nothing found" is not "safe".** It always comes with the list of what AeroTrace could not check.
- **It does not run your build or your tests.**
- C and C++ only. Tested on Linux and macOS.

Nothing leaves your machine: no network calls during analysis, no telemetry, and the HTML report loads
nothing from the internet.

## Install

Needs Python 3.11+ and [uv](https://docs.astral.sh/uv/). libclang comes with the package.

<!-- This URL only works once the repo is public. Until then, install from a local clone:
     uv tool install /path/to/aerotrace -->

```bash
uv tool install git+https://github.com/aayushswami123/aerotrace
cd your-c-repo
aerotrace doctor     # checks libclang and finds compile_commands.json
aerotrace index      # builds .aerotrace/index.db
```

## How it works

1. **Index.** AeroTrace reads your build's `compile_commands.json` and parses every file with libclang,
   using your real compile flags. It stores who calls what, and which globals and struct fields each
   function reads and writes, in `.aerotrace/` inside the repo.
2. **Map the change.** It maps a diff (or an edit an agent is about to make) to the functions, globals and
   fields it touches, including functions the change deletes and functions whose signature changes.
3. **Answer.** It follows callers and data uses outward, and answers with what breaks (will not build),
   what may be affected, and how sure each link is (`exact`, `probable` or `unknown`). Every answer also
   lists what it could not see.

```bash
aerotrace check                     # what your uncommitted change can break
aerotrace check main                # everything on this branch, compared with where it left main
aerotrace check --patch fix.diff    # a change that is not applied yet
aerotrace check --html report.html  # the same answer as one offline HTML file you can click through
```

`check` exits with 1 when something breaks (sure or probable) and 2 when it could not check. It does not
list tests yet; use `aerotrace tests` for that.

## Use it with your coding agent

Run one command inside your C/C++ repo:

| Assistant   | Command                                | What it changes                                                        |
|-------------|----------------------------------------|------------------------------------------------------------------------|
| Claude Code | `aerotrace install --client claude`    | `.mcp.json` and the edit-check hook in `.claude/settings.json`         |
| Cursor      | `aerotrace install --client cursor`    | `.cursor/mcp.json` in the repo                                         |
| Codex       | `aerotrace install --client codex`     | `~/.codex/config.toml` (or `$CODEX_HOME/config.toml`), for every repo  |

Add `--dry-run` to see the change first. Only the `aerotrace` entry is added or replaced; other servers,
hooks and settings are kept.

The agent gets these tools: `check`, `impact`, `diff_impact`, `signature_impact`, `data_users`,
`tests_for_change`, `callers`, `callees`, `find` and `index_status`. The first question builds the index.
After that, files you edit are re-indexed before each answer. While a build runs, the agent gets progress
or "still building, ask again shortly", never an answer from an old or half-written index.

### The edit check (Claude Code)

`install --client claude` also adds a `PreToolUse` hook on Edit, MultiEdit and Write. Before Claude edits
a C/C++ file, AeroTrace checks the edit as a patch:

- an exact break: Claude gets the full list (changed, broken and affected functions, data, tests to run);
- a probable break, or 10+ affected functions or data: a short warning;
- anything else: nothing.

It only warns. To block edits, add rules in `.aerotrace/rules.toml`:

```toml
[thresholds]
medium_affected = 10   # affected functions/data that make an edit "medium"
timeout_seconds = 5    # the check gives up after this and lets the edit through

[[rule]]
name = "core is protected"
paths = ["src/core/*"]   # globs on repo-relative paths (no paths = every file)
level = "medium"          # low | medium | high: the lowest level at which the rule fires
action = "block"          # warn (default) | block
```

If the index is missing or far out of date, the check fails, or `rules.toml` is invalid, the edit is
never blocked: Claude is told "AeroTrace couldn't check this edit (reason)". The one exception is a
protected path (a `level = "low"` block rule): it stays blocked, since protecting a path does not need
the index. `aerotrace hook off` / `aerotrace hook on` turn it off and on for the repo;
`aerotrace hook status` shows the state.

## Use it from the terminal

```bash
aerotrace impact setCommand                # what could break if this function changes
aerotrace callers dictAdd --depth 2
aerotrace callees processCommand
aerotrace signature lookupKeyReadOrReply   # every call site, and how each uses the return value
aerotrace data redisServer::hz             # every function that reads or writes a struct field or global
aerotrace find src/server.c:4221
aerotrace diff                             # what your uncommitted edits could break (new files included)
aerotrace tests                            # which tests to run for your edits (or --symbol FN)
aerotrace status                           # what is in the index and whether it is up to date
```

`tests` finds C/C++ test entry points in the index (GoogleTest `TEST`/`TEST_F` and fixture `SetUp`,
Unity `RUN_TEST`, CMocka `cmocka_unit_test`, and `main` in test files) and links them to the changed code
through callers. Each test is `exact` (only direct calls) or `probable` (a pointer, virtual or data link),
with the reason.

### Compile flags

For accurate results, give AeroTrace your build's `compile_commands.json`
(CMake: `-DCMAKE_EXPORT_COMPILE_COMMANDS=ON`; Make: run the build under [`bear`](https://github.com/rizsotto/Bear)).
AeroTrace looks in the repo root, `build*/`, `cmake-build-*/` and `out/*/`, or use `--build-dir`. It remembers
the folder, so later re-indexing (also by the MCP server) uses the same flags. Without one, it guesses
flags and says so in every answer.

- **Cross compilers.** `arm-none-eabi-gcc` means AeroTrace parses for `arm-none-eabi`, so target `#ifdef`s
  match the real build. The compiler is never run; only its name is read.
- **GCC-only flags** that clang does not know are ignored and listed, not counted as errors.
- **Response files** (`@flags.rsp`) are read when they are inside the repo or the build folder.

`aerotrace doctor` shows which database is used, other databases it found, the target, and source files
the build does not list.

## What the labels mean

| Label | Meaning |
|---|---|
| `exact` | A direct call the compiler resolved |
| `virtual` | A virtual call that may land on this override |
| `via_pointer` | A call through a function pointer whose type matches |
| `ambiguous` | Several functions share this name; this is one of them |
| `data` | Reads a global or field the changed function writes |
| `uses_changed` | Reads or writes a global or field whose declaration changed |
| `calls_removed` | Still calls a function the change removes; it will not build or link |

`check` sums these up as `exact`, `probable` or `unknown` for each item.

## License

Apache-2.0. See [LICENSE](LICENSE).
