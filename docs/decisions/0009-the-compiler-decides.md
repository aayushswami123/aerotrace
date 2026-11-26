# 0009: The compiler decides build and link breaks

- Status: accepted
- Date: 2026-11-25
- Replaces: the "never run a program named in the database" part of 0006

## Context

AeroTrace guesses whether an edit breaks the build from its own index (libclang facts plus our rules).
The gate (`bench/gate/`) shows where that goes wrong: inactive `#if` branches, files outside the build
list, macros, token pasting and C++ rules we don't model. These become false "safe" answers or UNKNOWN
answers. Copying more of the compiler's rules into AeroTrace will never reach "0 false safe".

The compiler and linker already know the answer. Until now we never ran them, because a crafted
`compile_commands.json` can name any program (SECURITY.md, ADR 0006).

## Decision

**The compiler and the linker decide build and link breaks. AeroTrace adds what they can't see.**

- For each file the edit reaches, AeroTrace compiles the edited code with a **trusted** compiler, using
  the flags from `compile_commands.json`, in every captured build variant. Link breaks are checked with
  the linker on the resulting objects, or with an equivalent symbol check.
- **Edits are applied in memory only.** The edited text goes to a temp folder (copies, or an overlay
  file AeroTrace writes there). Nothing is written into the user's tree except `.aerotrace/`. Objects go to a temp folder and are deleted.
- **Never run build scripts.** No `make`, `cmake`, `ninja`, configure scripts, or any program from inside
  the repo. Only a compiler driver and a linker.
- **Trusted compiler** means: a compiler found on the user's `PATH` or named in the user's own
  AeroTrace config (outside the repo), with a known driver name (`cc`, `gcc`, `g++`, `clang`, `clang++`,
  `<triple>-gcc`, ...). A compiler path inside the repo, or an unknown program name, is never run: that
  file's build answer is UNKNOWN with the reason and the fix (name a trusted compiler).
- **Flags are kept by an allowlist, not a blocklist.** Only flags known to be safe pass (`-I`, `-D`,
  `-U`, `-std=`, `-W...`, `-f...` and `-m...` without a path or plugin, `--target=`, `-isystem`, ...).
  Anything that can run or load another program is dropped and the answer says so: plugins
  (`-fplugin=`, `-fpass-plugin=`, `-Xclang -load`, `-Xclang -plugin`, `-mllvm -load`), `-B`, `-specs=`,
  `-wrapper`, `-fuse-ld=<path>`, `-Wl,-plugin=`, `-fuse-linker-plugin`. Response files are read only from
  inside the repo or the build folder. The list lives in one place and is tested.
- **Files the repo names are data only.** Headers, `-include` files and overlay files named in the flags
  are read by the compiler as input; they never pick the program that runs.
- **The environment is cleaned.** `PATH` entries inside the repo (and `.`) are ignored when finding the
  compiler. Variables that change which programs the compiler starts (`GCC_EXEC_PREFIX`,
  `COMPILER_PATH`, `CCC_OVERRIDE_OPTIONS`, ...) are removed.
- **BREAKS carries the compiler's own error text.** AeroTrace does not rewrite it into its own words.
- What the compiler can't see (function pointers, data flow, behaviour changes, tests, history) stays
  AeroTrace's own analysis, with confidence labels as before.
- If a build variant was not captured, or the compile has not finished in the time budget, the answer
  is UNKNOWN with that named reason. In CI there is no time budget.

## Consequences

- False "safe" on build and link questions should go to 0 for every reached file in a captured variant.
  The gate measures it.
- SECURITY.md changes: running a trusted compiler is allowed; running anything named only by the repo
  is still a security bug.
- Speed: a compile per reached file is slower than an index lookup. The hook keeps its budget and the
  check keeps running in the background past it (quality bar line 6).
- The result depends on the compiler version. It is part of the inputs, like the flags, so answers stay
  deterministic for the same code, flags, compiler and tool version.
- Not done yet. Until it is built, the code still never runs the compiler, and the README and
  SECURITY.md say so.
