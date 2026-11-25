# The gate

The gate answers one question: **when AeroTrace says "safe", is the code really safe?**

Each case is a tiny C/C++ project, a change (a patch), and a known truth from the compiler. The gate
runs AeroTrace on the change, builds the changed code with `cc`/`c++`, and compares the two.

This is **not** the audit's 112-case set. That harness was lost. The gate holds the cases that could be
saved: the audit's repro script (R1 to R7) and the cases written by the review passes of PRs #81 to #85
(see the audit in `docs/audit/AUDIT-2026-11.md`). 89 cases in all.

## Run it

```bash
make gate                                  # all cases, against the code in this checkout
make gate ONLY=a1-paste2                   # one case
make gate AT=/path/to/aerotrace            # another build (any command that works like `aerotrace`)
make gate-baseline                         # record a new baseline (on purpose, in a PR)
uv run python bench/gate/run_gate.py --help
```

**macOS with Apple clang only for now** (the gate prints one line and stops on anything else; `a1-asm` needs arm64). It is not part of CI. It takes about two minutes. It needs `cc`, `c++` and `git`, and no network. Each case runs in a temp
folder; the only file written in the repo is the optional `--markdown` report. A case with `needs = "arm64 macOS"` (`a1-asm`) is skipped loudly on other machines and the gate then fails.

## How the comparison works

`make gate` runs every case and compares the result with the saved baseline (`baseline.json`). It exits
**1** only if something got worse, in either mode:

- more false safe, more weak, more false alarm, or more false unknown (false unknown is also counted inside
  false alarm, and it must not go up on its own); or fewer correct;
- a case that errors (the baseline does not build, the tool crashes), or a case that is missing from the run
  or not in the baseline;
- any false safe that is not in the known limits (below), whatever the counts say.

It prints each case that changed class (from -> to), sorted by id. If something got better it says
"Better than the baseline: run `make gate-baseline` to record it" and still exits 0. `--only` / `ONLY=` runs
one case and does not compare with the baseline.

`baseline.json` has the per-case class and the counts for both modes. `BASELINE.md` is generated from the
same data, so the two cannot drift. **The baseline is only rewritten on purpose, in a PR:** run
`make gate-baseline` (it refuses to write if a case errors), read the diff, and commit both files.
The comparison logic is in `compare.py` and is tested in `tests/test_gate_compare.py`.

### Run against the build before the fixes

The audit's build is commit `7a24ef8`. Make a copy of it and compare:

```bash
git worktree add --detach /tmp/gate-base 7a24ef8 && (cd /tmp/gate-base && uv sync)
make gate-baseline BEFORE=/tmp/gate-base/.venv/bin/aerotrace
```

`BEFORE` adds "before" columns (exit code and bucket of the older build) next to the current ones.
Remove the worktree after (`git worktree remove --force /tmp/gate-base`).

## What a case does

1. Copy `base/` to a fresh folder, write `compile_commands.json` (`cc -c f.c`, or
   `c++ -std=c++17 -c f.cpp`), `git init`, commit.
2. Build the baseline. If it does not build, the case is an ERROR (never skipped).
3. `aerotrace index`.
4. **Patch mode:** `aerotrace check --patch change.patch`; keep the exit code (and the `--json` nodes).
5. Apply the patch (`git apply`), then **git mode:** `aerotrace check`.
6. Build the changed tree (every `.c`, `.cpp`, `.S` with `-ferror-limit=0`, then link). That is the truth.

## The five buckets

Exit codes: 0 = nothing found, 1 = BREAKS or MAY BREAK, 2 = UNKNOWN. Truth is the build of the changed tree.

| Bucket | Build | AeroTrace |
|---|---|---|
| correct | fails | exit 1 and a BREAKS / MAY BREAK item matches the failure |
| correct | passes | exit 0 |
| honest unknown | fails | exit 2 (UNKNOWN) |
| **false safe** | fails | exit 0. **This number must be 0.** |
| weak | fails | exit 1, but no BREAKS / MAY BREAK item is in a failing file or is a failing symbol |
| false alarm | passes | exit 1 |
| (false unknown) | passes | exit 2. Counted inside false alarm: `false alarm: N (of which false unknown: M)` |

Notes:

- "Weak" is only found when the `--json` nodes name files. A BREAKS item matches if its file is a file with
  a compile error, its name is a symbol the linker complained about, or its name appears in the compiler or
  linker messages. If there are no BREAKS nodes at all, the gate cannot tell, so it calls the answer correct
  and does not guess.
- Case `repro-r6` has no build truth (its patch does not apply). Any exit code except 0 is correct.
- Cases `repro-r2` and `repro-r7` are git mode only. `repro-r6` is patch mode only.
- Any other exit code is counted as "other" and printed.
- A case whose built result differs from `expect_build` in `case.toml` is listed under "notes".

## Known limits

Listed in `known_limits.toml`, tracked in https://github.com/aayushswami123/aerotrace/issues/93. They are
false safe on purpose and not fixed. Every run prints them with their current result, even when the gate
passes. If one stops being false safe, the run says so (an improvement: remove it from the list and record a
new baseline).

- `a1-paste2` (patch mode): the name is built by token pasting (`CMD(get)`) inside an inactive `#if` block.
- `cases1-ifdef2` (patch and git mode): the edit is only inside an inactive `#ifdef` block, so AeroTrace
  sees no change in the code it compiled.

## Layout

```
bench/gate/
  repro.sh              the audit's R1 to R7 script, unchanged (AT=<binary> bash repro.sh)
  run_gate.py           the runner (standard library only)
  baseline.json         the saved baseline (counts and the class of every case); the comparison reads it
  BASELINE.md           the human report, generated from baseline.json, with the commit that was measured
  known_limits.toml     the known false-safe cases
  compare.py            the comparison logic (pure functions)
  history/              records kept for reference
  cases/<id>/
    base/               the source files before the change
    change.patch        the change (a unified diff)
    case.toml           id, summary, origin, expect_build, failing_sites, flags
```

`case.toml` keys: `id`, `summary`, `origin`, `expect_build` (`fails`, `passes`, or `n/a`), `failing_sites`
(files or symbols the compiler or linker names), `build_flags` (extra flags for the truth build, for
example `-DX`), `compdb_flags` (flags in compile_commands.json), `not_in_build` (files left out of
compile_commands.json but still built for the truth), `modes`, `patch_applies`, `needs`.

The gate checks `case.toml` against the real build: if `expect_build` is wrong, or a `failing_sites` entry is not
among the files or symbols that really fail, the case is an ERROR and the gate fails.

Ids are the name of the script they came from plus the case name (`a1-paste2`, `cases1-ifdef2`).

## Where the cases came from

- `repro-r1` to `repro-r7`: the audit repro script (`repro.sh`).
- `a1-*`, `a2-*`, `odd-*`, `odd2-*`, `cases1-*`, `cases2-*`: the failing-build cases of the review-84 and
  review-85 passes, and some of their passing ones. The review tally is in
  `history/2026-11-25-review-tally.txt`.
- `cases3-*`: the harmless edits of review-84.
- `r81-*`, `r81b-*`, `r82-*`: the correct move, rename and prototype cases of review-81, 81b and 82. Only
  the passing-build ones are saved here. The failing-build ones of those passes are covered by the tests
  in the PRs.

Left out:

- `binary_c`, the 46th case of the review tally: it could not be found in the saved scripts. The gate has 45
  of the 46 failing-build cases of the tally.
- `cases1/h07b`: in the review script `git mv -q` fails, so the change was never made (the tally shows it as
  a build that passes). `cases2-h07b` is the same rename done correctly.
- The review-83 partial-clone cases: they need a generated git repo with a missing object.
  `tests/test_git_partial_clone.py` covers them.
- `cases1-override` was fixed: its header defined functions twice at link time, so the baseline did not
  link. The definitions are now `inline`.
- The hook part of R3 (`repro.sh` still runs it).

## All cases

`expect_build` is what the review found; the gate measures it again on every run.

| Id | What it checks | Build after the change | Origin |
|---|---|---|---|
| `a1-asm` | Function removed; it is also called from assembly (x.S, not in the build list), so the link fails. | fails | review-85 (PR #85), script a1 |
| `a1-dlsym` | Function removed; it is only named as a string for dlsym (runtime lookup). The build passes. | passes | review-85 (PR #85), script a1 |
| `a1-enum_inactive` | Enum member removed; its only use is in an inactive #ifdef X branch (build uses -DX). | fails | review-85 (PR #85), script a1 |
| `a1-ext_cxx` | Function removed; the only other mention is in x.cxx, which nothing compiles. The build passes. | passes | review-85 (PR #85), script a1 |
| `a1-ext_def` | Function removed; the only other mention is in x.def, which nothing compiles. The build passes. | passes | review-85 (PR #85), script a1 |
| `a1-ext_hh` | Function removed; the only other mention is in x.hh, which nothing compiles. The build passes. | passes | review-85 (PR #85), script a1 |
| `a1-ext_inc` | Function removed; the only other mention is in x.inc, which nothing compiles. The build passes. | passes | review-85 (PR #85), script a1 |
| `a1-ext_ipp` | Function removed; the only other mention is in x.ipp, which nothing compiles. The build passes. | passes | review-85 (PR #85), script a1 |
| `a1-field_inactive` | Struct field removed; its only use is in an inactive #ifdef X block (build uses -DX). | fails | review-85 (PR #85), script a1 |
| `a1-field_outside` | Struct field removed; it is used in extra.c, which is not in compile_commands.json. | fails | review-85 (PR #85), script a1 |
| `a1-macro_inactive` | Macro removed; its only use is in an inactive #ifdef X block (build uses -DX). | fails | review-85 (PR #85), script a1 |
| `a1-macrohdr` | Function removed; it is named in a macro body in m.h, and main.c uses that macro. | fails | review-85 (PR #85), script a1 |
| `a1-macrohdr_unused` | Function removed; it is named in a macro body in m.h, but no compiled file uses the macro. The build passes. | passes | review-85 (PR #85), script a1 |
| `a1-method_inactive` | C++ method removed; its only call is in an inactive #ifdef X block (build uses -DX). | fails | review-85 (PR #85), script a1 |
| `a1-method_outside` | C++ method removed; it is called in main.cpp, which is not in compile_commands.json. | fails | review-85 (PR #85), script a1 |
| `a1-param_inactive` | Parameter type changed from int to int*; the only call is in an inactive #ifdef X block (build uses -DX). | fails | review-85 (PR #85), script a1 |
| `a1-paste` | Function removed; its only caller reaches it by token pasting (CMD(get) becomes getCommand()). | fails | review-85 (PR #85), script a1 |
| `a1-paste2` | Function removed; token pasting inside an inactive #ifdef X block (build uses -DX). Known limit. | fails | review-85 (PR #85), script a1 |
| `a1-renamed_fn` | Function renamed; the caller in main.c is not in compile_commands.json. | fails | review-85 (PR #85), script a1 |
| `a1-rettype_inactive` | Return type changed from int to a struct; the only caller is in an inactive #ifdef X block (build uses -DX). | fails | review-85 (PR #85), script a1 |
| `a1-typedef_outside` | Typedef removed; it is used in extra.c, which is not in compile_commands.json. | fails | review-85 (PR #85), script a1 |
| `a2-const_rm` | C++ method loses const; main.cpp calls it on a const object. | fails | review-85 (PR #85), script a2 |
| `a2-ext_cxx` | Function removed; x.cxx still calls it and main.c includes x.cxx under #ifdef X (build uses -DX). | fails | review-85 (PR #85), script a2 |
| `a2-ext_def` | Function removed; x.def still calls it and main.c includes x.def under #ifdef X (build uses -DX). | fails | review-85 (PR #85), script a2 |
| `a2-ext_hh` | Function removed; x.hh still calls it and main.c includes x.hh under #ifdef X (build uses -DX). | fails | review-85 (PR #85), script a2 |
| `a2-ext_inc` | Function removed; x.inc still calls it and main.c includes x.inc under #ifdef X (build uses -DX). | fails | review-85 (PR #85), script a2 |
| `a2-ext_ipp` | Function removed; x.ipp still calls it and main.c includes x.ipp under #ifdef X (build uses -DX). | fails | review-85 (PR #85), script a2 |
| `a2-knr` | K&R definition gains a parameter; the caller uses an empty-parentheses declaration. The build passes. | passes | review-85 (PR #85), script a2 |
| `a2-noexcept_rm` | noexcept removed from a function; main.cpp stores it in a noexcept function pointer. | fails | review-85 (PR #85), script a2 |
| `a2-ok_add_param` | Correct change: a parameter is added and the declaration, definition and caller all follow. | passes | review-85 (PR #85), script a2 |
| `a2-ok_paramname` | Correct change: a parameter is renamed in the declaration and definition only. | passes | review-85 (PR #85), script a2 |
| `a2-ok_rename_fn` | Correct change: a function is renamed in the declaration, definition and caller. | passes | review-85 (PR #85), script a2 |
| `a2-ok_rename_global` | Correct change: a global is renamed in the header, definition and user. | passes | review-85 (PR #85), script a2 |
| `a2-ptrconst` | Parameter int* becomes const int*; main.c passes the function as an int* callback (build uses -Werror). | fails | review-85 (PR #85), script a2 |
| `a2-static_macro` | Function made static through a macro (#define PRIV static); another file calls it. | fails | review-85 (PR #85), script a2 |
| `a2-static_prevline` | Function made static with 'static' on the line above its name; another file calls it. | fails | review-85 (PR #85), script a2 |
| `cases1-build_file` | A build file (Makefile) changes and no C file does. The C sources still build. | passes | review-84 (PR #84), script cases1 |
| `cases1-def_table` | An X-macro list file (ops.def) is broken by an unbalanced parenthesis. | fails | review-84 (PR #84), script cases1 |
| `cases1-def_table2` | An entry is removed from the X-macro list ops.def; main.c still uses OP_SUB. | fails | review-84 (PR #84), script cases1 |
| `cases1-enum` | Enum member BLUE removed; main.c uses it. | fails | review-84 (PR #84), script cases1 |
| `cases1-h07` | Header lib.h deleted but main.c still includes it. | fails | review-84 (PR #84), script cases1 |
| `cases1-ifdef` | Signature changed; the only caller is under #ifdef USE_F (build uses -DUSE_F). | fails | review-84 (PR #84), script cases1 |
| `cases1-ifdef2` | The edit sits only inside an inactive #ifdef DBG block (build uses -DDBG). Known limit. | fails | review-84 (PR #84), script cases1 |
| `cases1-l01` | Signature changed; the caller in main.c is not in compile_commands.json. | fails | review-84 (PR #84), script cases1 |
| `cases1-l01b` | Function removed; the caller in main.c is not in compile_commands.json. | fails | review-84 (PR #84), script cases1 |
| `cases1-macro_sa` | Macro value lowered; a _Static_assert in main.c now fails. | fails | review-84 (PR #84), script cases1 |
| `cases1-newfile_dup` | A new file extra.c defines calc again (duplicate symbol). | fails | review-84 (PR #84), script cases1 |
| `cases1-override` | Base-class virtual method removed; D::f override in main.cpp no longer overrides. Definitions made inline so the baseline links. | fails | review-84 (PR #84), script cases1 |
| `cases1-override2` | Base-class virtual method removed (definitions in b.cpp); D::f override in main.cpp no longer overrides. | fails | review-84 (PR #84), script cases1 |
| `cases1-static_r7` | Function made static; another file calls it. | fails | review-84 (PR #84), script cases1 |
| `cases1-struct_sa` | Struct field removed; a _Static_assert on sizeof in main.c now fails. | fails | review-84 (PR #84), script cases1 |
| `cases1-struct_sa_c` | Struct field removed; a _Static_assert on sizeof in the same .c file now fails. | fails | review-84 (PR #84), script cases1 |
| `cases1-table_r2` | Function removed; it is still named in a function table in main.c. | fails | review-84 (PR #84), script cases1 |
| `cases1-typedef` | Typedef changed from int to long; main.c converts a pointer to it (build uses -Werror=incompatible-pointer-types). | fails | review-84 (PR #84), script cases1 |
| `cases2-h07b` | Header renamed from lib.h to lib2.h; main.c still includes lib.h. | fails | review-84 (PR #84), script cases2 |
| `cases3-blank` | Harmless edit: A blank line is added in lib.c. | passes | review-84 (PR #84), script cases3 (harmless edits) |
| `cases3-body` | Harmless edit: The body of f changes. | passes | review-84 (PR #84), script cases3 (harmless edits) |
| `cases3-filecomment` | Harmless edit: A file-level comment is edited. | passes | review-84 (PR #84), script cases3 (harmless edits) |
| `cases3-fncomment` | Harmless edit: A comment inside f is edited. | passes | review-84 (PR #84), script cases3 (harmless edits) |
| `cases3-hdrcomment` | Harmless edit: A comment is added in the header. | passes | review-84 (PR #84), script cases3 (harmless edits) |
| `cases3-include` | Harmless edit: An #include <string.h> is added. | passes | review-84 (PR #84), script cases3 (harmless edits) |
| `cases3-local` | Harmless edit: A local variable is added in f. | passes | review-84 (PR #84), script cases3 (harmless edits) |
| `cases3-newstatic` | Harmless edit: A new, unused static function is added. | passes | review-84 (PR #84), script cases3 (harmless edits) |
| `cases3-reformat` | Harmless edit: The signature of f is reformatted over two lines. | passes | review-84 (PR #84), script cases3 (harmless edits) |
| `odd-ctor_outside` | Constructor definition removed; it is used in main.cpp, which is not in compile_commands.json. | fails | review-85 (PR #85), script odd |
| `odd-fnptr_rename` | Function-pointer variable renamed in lib.h and lib.c; main.c still uses the old name. | fails | review-85 (PR #85), script odd |
| `odd-multi_decl_remove` | One name is removed from a shared declaration (int a, hits); main.c uses the removed one. | fails | review-85 (PR #85), script odd |
| `odd-multi_decl_rename_first` | Correct change: the first name in a shared declaration is renamed everywhere. | passes | review-85 (PR #85), script odd |
| `odd2-fnptr_rename_all` | Correct change: a function-pointer variable is renamed in all three files. | passes | review-85 (PR #85), script odd2 |
| `r81-c12` | Correct change: only an attribute is added to a prototype. | passes | review-81 (PR #81), case c12 |
| `r81-c13` | Correct move: a function moves to the end of another .c file; prototype stays. | passes | review-81 (PR #81), case c13 |
| `r81-c14` | Correct move: same as c13 with a differently shaped diff. | passes | review-81 (PR #81), case c14 |
| `r81-c4` | Correct move: a function moves to other.c and its prototype stays. | passes | review-81 (PR #81), case c4 |
| `r81-c4d` | Correct move: same as c4 with a differently shaped diff. | passes | review-81 (PR #81), case c4d |
| `r81b-a8` | Correct change: a prototype that nobody uses is removed. | passes | review-81b (PR #81), case a8 |
| `r81b-c1` | Correct change: a prototype moves to another header that the callers include. | passes | review-81b (PR #81), case c1 |
| `r81b-c2` | Correct change: a function becomes static; all callers are in the same file; the prototype is removed. | passes | review-81b (PR #81), case c2 |
| `r81b-n0` | Correct move: a function moves to a new file with the same signature. | passes | review-81b (PR #81), case n0 |
| `r82-comment` | Correct rename: a function is renamed everywhere; the old name stays only in a comment. | passes | review-82 (PR #82), case comment |
| `r82-dmove` | Correct move: calc moves from lib.c to other.c; main.c declares it. | passes | review-82 (PR #82), case dmove |
| `r82-dstatic` | Harmless: a new static function calc is added in other.c while lib.c keeps its own calc. | passes | review-82 (PR #82), case dstatic |
| `r82-overload` | Harmless: new overloads with the same name are added in a C++ file. | passes | review-82 (PR #82), case overload |
| `repro-r1` | Function removed together with its header prototype; main.c still calls it. | fails | audit repro.sh, case R1 |
| `repro-r2` | Function removed; it is still named in a function table. Git mode only (edit applied, then check). | fails | audit repro.sh, case R2 |
| `repro-r3` | Function-like macro changes arity in a header (the hook part of R3 is not run by the gate). | fails | audit repro.sh, case R3 |
| `repro-r4` | Parameter changes from int to int*; the caller passes an int. | fails | audit repro.sh, case R4 |
| `repro-r5` | A duplicate definition of calc is added in another file. | fails | audit repro.sh, case R5 |
| `repro-r6` | A patch that does not match the file. No build truth: any exit code except 0 is correct. | n/a | audit repro.sh, case R6 |
| `repro-r7` | Function made static while another file calls it. Git mode only. | fails | audit repro.sh, case R7 |
