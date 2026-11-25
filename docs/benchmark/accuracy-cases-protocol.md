# Accuracy benchmark: how the 30 cases are scored

Status: written and committed **before any compared tool was run** on the cases. Truth was built
first, from the compiler and linker only.

Cases: `bench/cases/CASES.md` (merged in #63). Plan: `docs/plans/dashboard-and-benchmark.md` section 7.

## The question

For each case, every tool answers the same question an agent asks before an edit: **"what will this
change break?"** Each tool is used the way it normally is, on the **unchanged** source tree. AeroTrace
is the one tool that reads the patch itself, because that is how it is normally used (`aerotrace check`).

## Truth (`bench/truth/truth.py`)

- In a copy-on-write clone of a fully built baseline, apply the case's patch and rebuild with `make -k`
  (keep going after errors) and `-ferror-limit=0` (report every error).
- Every compile error and every link error ("Undefined symbols", the referencing function) is a
  break. Nothing else is: warnings are not breaks.
- If any file fails to compile, the program is not linked, so link-only breaks for that case aren't
  seen. Such cases are marked `link_checked: false`.
- Errors on lines the patch itself changed are part of the change, not breaks. They're listed apart.
- "Might break" cases (`behaviour`) have no build errors by design. Their truth is the direct callers
  in compiled code that use the returned value, checked by hand with the line that shows it
  (`bench/truth/might_break.json`).
- Build setup, exactly as run: `bench/truth/README.md` and the `setup` block of `truth.json`.

## Sites: one unit for every tool

Answers and truth are compared as **sites**, not lines. A site is the function that contains the
line (`file:function`), or the file-scope table being initialized (`file:@name`). Rows of Redis's
`commands.def` are all `src/commands.def:@redisCommandTable`. Lines that fall in neither (a prototype,
an `extern`, a struct member, a `#define`) are sites of their own (`file:@lineN`). They count as
answers, and they are never truth (a declaration can't fail to build without a use failing).

Every tool's file:line answers are turned into sites by the same code (`bench/truth/sites.py`).
Two kinds of answer are dropped for every tool:

- lines the patch itself changes (the definition and prototypes being edited), because they are the
  change, not something it breaks;
- the body of the changed function itself (its own recursive call, for example).

## The tools, as normally used

| tool | how it is asked | what counts as its answer |
|---|---|---|
| AeroTrace (this commit) | `aerotrace check --patch <case.diff> --json`, on an index of the unchanged tree built with `aerotrace index` and the build's `compile_commands.json` | **claimed breaks:** nodes with status `breaks` (certainty `exact` or `probable`, that is BREAKS and MAY BREAK). Also reported: **in the answer**, every node it reports as possibly hit: BREAKS, MAY BREAK, AFFECTED and UNKNOWN (not NO EFFECT FOUND, which means checked and nothing found) |
| ripgrep 15.2.0 | `rg -n -w <name>` over the build's files (functions, globals, macros: the name; fields: the field name) | every matching line |
| clangd 21.0.0 (Apple) | `textDocument/references` (declarations not included) at the symbol's definition, with `--background-index` on the build's `compile_commands.json`, after indexing finishes | every reference location |
| cscope 15.9 | database built with `cscope -b -q -k` over the build's files; query `cscope -d -L0 <name>` ("find this C symbol") | every result line |

On the 3 "might break" cases nothing fails to build, so AeroTrace's claim there is every node it marks
BREAKS, MAY BREAK or AFFECTED.

For ripgrep, clangd and cscope every line is a possible break, because those tools don't say what
breaks. AeroTrace does, so its "claimed breaks" are scored. Its whole answer is scored too, so the
benefit of labelling can be seen.

## Measures

Per case and tool, with T the truth sites and A the answer sites:

- **found** = |A ∩ T|, **missed** = |T \ A|, **false alarms** = |A \ T|.
- **Recall** = found / |T|, summed over cases (micro-average), per repo and per level (easy, hard). A
  case with no truth sites adds nothing to recall and only adds false alarms.
- **Time** = wall time of the query only, median of 3 runs. One-off setup is reported apart: AeroTrace
  index, clangd background index, cscope database. ripgrep has no setup.
- **Tokens** = Claude tokens in the text an agent would read:
  - AeroTrace: the text the MCP `check` tool returns (`ui.render_text`, plain, lists capped at 15 with
    "N more" lines).
  - ripgrep and cscope: their output as printed.
  - clangd: one `file:line: <source line>` per reference.
  Counted with Anthropic's token-counting endpoint (benchmark only, never in the product). Each count is
  cached by the sha256 of the text, so the scores can be recomputed offline.
- Where AeroTrace's 15-item cap hides a claimed break from the text, the case is flagged. Recall is
  computed on its full JSON answer either way.

## Reporting rules

- Easy and hard cases are always shown apart. Any headline number says which mix it comes from
  (for example "hard cases, 18 of 30").
- Every case is listed, with each tool's found / missed / false alarms, including the ones AeroTrace
  loses or ties.
- Nothing is dropped after the fact. A case the harness gets wrong is fixed and re-run for **all**
  tools, and the fix is logged in `RESULTS.md`.

## Fixes made after the first scoring (logged as the rules require)

All fixes were applied to every tool and every case, and everything was re-scored. No tool answer
changed. Only how answers and truth are read changed.

1. **"In the answer" for AeroTrace** first included its NO EFFECT FOUND nodes, which mean "checked,
   nothing found". That misread the tool, so they are left out now. The field was recomputed from the nodes already
   saved in each raw answer; AeroTrace was not run again.
2. **Link-error sites.** The linker names the function after inlining (curl builds with `-O2`, so a
   static helper's use shows up under its caller). A link error's site is now where the object's
   source file uses the missing symbol, named by the same site rules as everything else.
3. **Tables after an inline struct type** (`} const maybe_feature[] = {`) were not seen as tables, so
   their rows got `@lineN` sites. They are tables now.

4. **curl's truth build target.** The default `make` also builds the test-only `libcurlu` and
   `libcurltool` with `-DUNITTESTS`, code that is not in the build the tools were given. The truth is now
   built from `libcurl` and the `curl` program only, as `CASES.md` defines. Found in review; it removed
   one break site (curl-01, inside `#ifdef UNITTESTS`).

Each fix makes the truth and the answers describe the same build and use the same names. None was chosen by looking at
which tool it helps.
