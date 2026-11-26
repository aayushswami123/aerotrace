# 0006: Compile flags on real builds, without running the user's tools

- Status: proposed; the "never run a program named in the database" part is replaced by 0009
- Date: 2026-11-08

## Context

Our users build legacy and embedded C/C++ code, often with cross compilers (`arm-none-eabi-gcc`) and GCC-only
flags. libclang parses for the host by default. With `-mfloat-abi=hard` and no target it refuses the whole
file, so every function in it disappeared from the index. Unknown GCC flags were counted as parse errors,
which made answers look broken. Response files were not read, several databases were chosen between in
silence, and a refresh by the MCP server forgot `--build-dir`, falling back to guessed flags.

The obvious fix for targets and system headers is to run the compiler from the database (`<cc> -v -E`).
SECURITY.md says code execution triggered by analysing a repo is a security bug, and a crafted
`compile_commands.json` can name any program.

## Decision

- Never run a program named in the database. Read the target from the compiler's name
  (`<triple>-gcc`, `<triple>-g++-12`, ...) and pass `--target=<triple>` when the flags have none.
- If libclang still refuses a file, parse it once more without its `-m` flags, and record that in the
  index so every answer can say that target `#ifdef`s may differ.
- Diagnostics about unknown flags are kept as a list of ignored flags, not as parse errors.
- Read `@file` response files only inside the repo or the database's folder (one the user pointed us at).
- Store the build dir, excludes and a hash of the database in the index metadata. Refreshes reuse the
  settings, and a changed database triggers a rebuild.
- Record what the flags do not cover (other databases, sources the build does not list, missing headers)
  and show it in the limits of every answer.

## Consequences

- ARM builds parse with the right target macros. In our fixture, a call that exists only in the
  Cortex-M4F build is found; with an unknown compiler name it is missed, and the answer says so.
- System headers still come from the host compiler. They work for ARM, but some host SDKs reject other
  targets (RISC-V on macOS). Those show up as parse errors, not silently. A later option could let the user
  name a sysroot or a trusted compiler to ask.
- No schema change: everything new lives in the `meta` table. The parser version moved to 4, so caches
  are rebuilt once.
