# Plan: `aerotrace check`: data and signature effects of a diff (item 5, Part C)

Branch `feat/check`, from `origin/feat/data-users` (PR #41, Part B, not merged yet). It also needs
`origin/feat/launch-ui` (PR #38: `ui/schema.py`, terminal renderer, `--html`) and the #14 fix that is on
`launch-candidate` but not on `feat/data-users`. Rebase on `launch-candidate` once #41 and #38 merge
(see O1 if they have not merged when the build needs them).

## Goal

`aerotrace check [BASE | --patch FILE]` answers "what can this diff break?" with **data** effects, not
only call effects:

- every changed function whose **signature** changed: call sites, how results are used, places that name
  it as a value, pointer calls of its type (Part A, `analysis/signature.py`), judged against the **new**
  signature;
- every changed, removed or renamed **struct field or global**: its users (Part B, `analysis/data.py`);
- everything `diff` already finds (callers, removed functions, data coupling);
- each result `exact` / `probable` / `unknown` with a reason; blind spots stated;
- rendered through the launch UI contract (`aerotrace-check` v1): terminal text, `--json`, `--html`.

Patch mode also parses the **patched text in memory**, which fixes #13 (false "could not follow" note).

## 1. Detecting what changed: old outline vs new outline

Today an `Outline` (`index/history.py`) has functions, prototypes and variables with names and lines only.
Part C compares two outlines of every changed code file, so both sides are described the same way:

| mode | old side | new side |
|---|---|---|
| git | `Outliner.at(base, path)` (git show, as today) | `Outliner.now(path)` (disk) |
| patch | `Outliner.now(path)` (disk = the index's version) | `Outliner.text(path, patched)` (in memory) |

Only files with a changed line inside or next to a declaration are outlined twice; files whose changes are
all inside function bodies and do not touch a signature line still need both sides only for the lines of
the function head (see 7 for the shortcut).

### 1.1 Outline additions (not stored; no index or schema change, `PARSER_VERSION` unchanged)

`parse.function_outline` gains, per function and prototype: `signature` (same format as
`functions.signature`: `result displayname [const]`), `returns` (spelled result type), `params`:
`[{name, type, canonical, has_default}]`, `variadic`, `head` (first and last line of the declarator, so a
change inside the body is not taken for a signature change). Per variable: `usr`, `name`, `container`,
`kind` (`field` / `global`), `type`, `canonical`, `position` (index of the field in its record).
`Outliner.text(rel, content)` is the public form of today's `_parse`.

### 1.2 Matching old and new

- **Functions**: same USR. C++ overloads change USR when a parameter type changes, so if a USR is on one
  side only, fall back to the same qualified name in the same file when exactly one is unmatched on each
  side ("same function, new signature"). Several candidates: report removed + added (never pick).
- **Fields**: same record (container) and name. **Globals**: same USR, else same name in the same file.

### 1.3 What counts as a change

| change | detected as | effects |
|---|---|---|
| parameter added / removed | param count differs (ignoring `void`; variadic and defaults counted separately) | signature effects |
| parameter retyped | spelled type differs after whitespace normalising | signature effects |
| parameters reordered | same type list as a multiset, names permuted (`(int a, int b)` -> `(int b, int a)`) | signature effects; every call is listed (each must swap its arguments) |
| return type changed | spelled `returns` differs | signature effects (result uses) |
| only names of parameters changed | types equal, names differ, not a permutation | no signature effect; function is "changed" as today |
| `static` added / removed, `const` method | from `signature` / linkage | signature effects |
| field / global retyped | spelled `type` differs | data effects, `uses_changed` |
| same spelling, other canonical type | (a typedef changed elsewhere) | not seen here; the typedef line stays under "could not follow" |
| other spelling, same canonical type (`long` -> `int64_t` on this target) | spelled differs, canonical equal | listed, users `affected` **probable**, reason "same underlying type on this build; behaviour should not change" (O3) |
| field / global removed | old side only | removed node; users are breaks (see 3) |
| field / global added | new side only | new node; for a field: layout blind spot + globals that may set it by position (Part B `by_position`, unknown) |
| renamed | removed + added in the same record (field) or file (global), same type, same position | removed + added, with the hint "maybe renamed to Y" on both; never merged |
| function renamed | removed + added, same file, same signature, overlapping old/new lines | same: removed + new, with a hint |

A changed declaration line that is neither (a `struct x {` line, a typedef, a macro) stays in
`not_followed.outside`, as today.

## 2. Patch mode: the new side (fixes #13)

1. `_matches_disk` already proves the patch's old lines equal the disk text.
2. New `unidiff.apply(disk_text, fd) -> str`: drop the removed old lines, insert the added lines at their
   gaps, keep the rest. Checked: the result's line count equals `len(disk) - len(removed) + len(added)`,
   and every `fd.new_text[n]` sits at line `n`; otherwise the file goes to `mismatched` (never mapped to
   the wrong lines).
3. Outline the patched text with the same flags (`Outliner.text`).
4. Map the **added** lines (new numbering) onto the patched outline, exactly as git mode maps removed
   lines onto the old outline: a line on a declaration that matches the index (via 1.2) marks it and is
   not "could not follow". This is what flips #13: the new text of `long long stat_numcommands;` lands on
   `redisServer::stat_numcommands`.
5. Bonus, from ADR 0005: a function in the index that is missing from the patched outline is **removed**
   (not "changed" when git splits the deletion across two functions). A function only in the patched
   outline is **new** (`AddedFn`, a new optional `DiffImpact.added` list; today its lines are "could not
   follow").

`diff.analyse` gets one keyword argument, `patched: Callable[[str, str], Outline | None] | None`
(`Outliner.text`). The CLI, MCP and the realrepo harness `_check_diff` pass it (a one-line change to the
harness, no fact weakened). Then the two #13 facts XPASS and their `known_bug` is removed:

- `redis-diff-field-type-change-no-false-note` (`tests/realrepo/facts_redis.py`)
- `sqlite-diff-field-type-change-no-false-note` (`tests/realrepo/facts_sqlite.py`)

Note: #13 is 2 of the 3 known golden failures; the third is #12 (`== f` counted as address taken), which
Part C does not touch. After Part C the golden run has **1** known failure (#12).

## 3. Effects, and how sure each one is

New module `src/aerotrace/analysis/check.py` (no logic in `cli.py` / `mcp/` / `ui/`):

```python
@dataclass
class SigChange:  fn_id; old: dict; new: dict; kinds: list[str]  # added/removed/retyped/reordered/returns/linkage
@dataclass
class DeclChange: old: dict | None; new: dict | None; var: Var | None; kind: str  # retyped/removed/added/renamed
                  hint: str = ""
@dataclass
class CheckResult:
    diff: DiffImpact
    signatures: list[tuple[SigChange, SignatureImpact]]
    decls: list[tuple[DeclChange, DataUsers | None]]
    blind_spots: list[tuple[str, str, str | None]]   # (text, scope, node id)

def run(conn, root, diffs, *, mode, base, depth, old, now, patched) -> CheckResult
def to_contract(res: CheckResult, tool: str, cap: int = 5000) -> dict  # passes ui.schema.check
```

### 3.1 Signature effects (from `signature_impact(conn, fn)`, judged against the new signature)

In **git mode** the index is the new code: a call that no longer compiles is a `call_sites` row with
detail "does not compile" (ADR 0007). In **patch mode** the index is the old code: we compare each call's
`n_args` and result use with the new signature.

| site | status | certainty |
|---|---|---|
| git: call site marked "does not compile" | breaks | from the link (`exact`; `ambiguous` / `virtual` -> probable) |
| patch: `n_args` < required or > total params (not variadic) | breaks | same |
| new return type `void`, result used (stored, compared, checked, passed, returned, used) | breaks | same |
| call compiles by count, a parameter or the return type was retyped or reordered | affected | link certainty; reason names the parameter: "parameter 2 is now `long` (was `int`)" |
| the caller is itself edited by the patch (patch mode) | breaks -> **probable** | "this function is changed by the patch too; check its call" |
| pointer call of its type (`pointer_sites`) | affected | probable ("calls a pointer of the old type") |
| place that names it as a value (`address_sites`: table, callback) | affected | exact ("stores it as a `T`; the pointer type must change too"). Not a break: C compiles it with a warning |
| callers further up (`upstream`) | affected | weakest link on the path |

### 3.2 Data effects (Part B API)

| change | call | users become |
|---|---|---|
| field / global retyped | `data_users(conn, var.id)` (declaration question) | `affected`, link `uses_changed`, from `of_access` (exact; ambiguous -> probable) |
| field removed, git mode | `gone_fields(conn, "S::X")` -> `data_users_of(conn, v)` | `broken` rows: **breaks**, exact (record matches) or probable (typedef / base) |
| field removed, patch mode | `data_users(conn, var.id)` | every use and initializer site: **breaks** exact; probable when the patch also edits that function |
| global removed, patch mode | `data_users(conn, var.id)` | breaks, as above |
| global removed, git mode | (index records nothing: "use of undeclared identifier" is #25) | removed node + change-scope blind spot "uses of a removed global are not recorded (#25); search for X by hand" |
| field added / reordered | `by_position` of its record | `unknown` ("may set fields by position") + layout blind spot |
| any | `escapes` | `unknown` ("its address is passed to foo()") |
| any | initializer sites | affected exact (breaks when the field is removed) |

Upstream of data users: Part B defaults (0 for fields, 2 for globals), so a changed `server`-wide field
does not explode. The existing `data` coupling seeds in `diff._seeds` stay (`data` link -> probable).

### 3.3 One node per function

A function reached in several ways keeps its strongest status (breaks > affected > unknown), then its
strongest certainty, then the shortest distance; `why` is that reason, plus "(and N other links)" when
there are more. Deterministic: ties by (file, line, id).

## 4. The UI contract, field by field (`aerotrace-check` v1)

| field | filled from |
|---|---|
| `format`, `version`, `tool` | constants; `aerotrace <__version__>` |
| `status` | `no_index` (no index and could not build one: message says "run `aerotrace index`"), `index_error` (index unreadable / build failed: message), `nothing_to_check` (empty diff or no C/C++ file), else `ok` |
| `message` | for the non-ok statuses only |
| `change.mode`, `base` | `DiffImpact.mode`, `base` (null in patch mode) |
| `change.files`, `other_files` | `files`, `ignored` |
| `changed_functions`, `new_functions`, `removed_functions` | `changed` by state (+ `added` in patch mode), `removed` (+ patch-mode removed) |
| `changed_declarations` | number of `DeclChange` (incl. removed and added) |
| `change.not_followed` | `outside` (`lines`, `other_side_lines` = `Outside.other_side`), `not_indexed`, `deleted`, `mismatched`, `unreadable`, `new_files`: copied, every one |
| `nodes[].id` | index ids (`functions.id`, `variables.id`). Removed function: `removed:{file}:{line}:{name}` (the key `diff.py` already uses); removed field / global: same pattern with its qualified name and old line. New in a patch (not in the index): `new:{file}:{line}:{name}` (O2) |
| `kind` | `function` / `field` / `global` / `static_local` |
| `status`, `certainty`, `why`, `lines` | section 3 |
| `link` | raw label: `exact`, `calls_removed`, `uses_changed`, `virtual`, `ambiguous`, `via_pointer`, `data`; null for changed nodes |
| `distance` | 0 for changed / new / removed only; call sites and data users 1; upstream from `Hit.distance` (+1 for signature upstream) |
| `via` / `via_name` | `Hit.via` is a qualified name: mapped to a node id through the nodes present; 0 or 2+ matches -> `via` null, `via_name` kept. For a call site or data user: the changed function / variable's id |
| `edges` | `calls` caller -> callee for every node with a `via` function; `reads` / `writes` fn -> variable for data users; certainty = the node's |
| `sections.functions`, `data` | node ids by kind, in node order |
| `tests_to_run`, `untested_changes` | `null` ("Tests: not known yet") until the test map (PR #34) lands |
| `sections.blind_spots` | scope `change`: diff notes (outside, unreadable, address taken, clash, depth reached), Part A notes per changed signature, Part B notes per changed declaration (with its node), #25 note, layout note. Scope `index`: `graph.index_limits`. Deduplicated by text, sorted |
| `summary` | counted over **all** nodes before the cap: `breaks_exact`, `breaks_probable`, `affected_direct` (distance 1), `affected_further`, `unknown`, `no_effect` (always 0: Part C sends no grey nodes), `not_followed` = entries in `not_followed` + change-scope blind spots |
| `omitted.nodes` | dropped by the 5,000-node cap (lowest ranked first: affected further up, then unknown); edges and `via` to dropped nodes are removed, `via_name` stays |
| `proof` | `index_files`, `index_functions` (COUNT from the index), `depth`, `stopped_early` = any walk truncated; optional together: `build_files` (entries in compile_commands.json), `build_files_checked` (parsed), `build_files_with_errors`, `outside_build` (repo sources not in it), `outside_build_examples` (first 3 sorted paths); `outside_build_files` (all, sorted) only with `--all` |

Nodes sorted by (status rank, certainty, distance, file, line, id), as the contract says.

## 5. `diff` vs `check`, CLI and MCP

- **`aerotrace diff` keeps its output.** Owner rule: don't change defaults users rely on. Data and
  signature effects are only in `check`. The only `diff` changes are bug fixes the owner already
  approved: the #13 false note goes away, and in patch mode a function the patch deletes is "removed"
  and one it adds is listed instead of "could not follow". `tests/golden/diff_c_multi.txt` must not
  change; if it does, the build stops and reports.
  Why not add sections to `diff`: two commands with overlapping long answers confuse agents; `check` is
  the new front door, `diff` stays the stable, smaller answer.
- **`aerotrace check [BASE] [--patch FILE] [--depth N] [--limit N] [--all] [--json] [--html OUT] [--root DIR]
  [--build-dir DIR]`**: text via `ui.render_text(data, **ui.style_for(sys.stdout, os.environ), limit)`;
  `--json` prints the contract object; `--html` calls `ui.write_html`. Exit codes (UI plan suggestion):
  0 = nothing breaks, 1 = something breaks (exact or probable), 2 = could not check.
- **MCP**: a new `check(base="HEAD", patch="", depth=5, limit=40)` tool, text output (`render_text`, no
  colour, ASCII icons), description: "Before you finish an edit, call this with your patch: it lists what
  will not build, what may change behaviour, and what AeroTrace could not follow." `diff_impact` is
  unchanged. One line in `INSTRUCTIONS`.
- **Hook (item 7)** calls `analysis.check.run(... mode="patch")` + `to_contract` in-process, not MCP.

## 6. Fixtures (written to trip it up)

New `tests/fixtures/c_check/` (small; Part A/B fixtures are not touched), patches as files in
`tests/fixtures/c_check/patches/`, git cases built in a temp git repo (as `test_diff.py` does):

| case | expected |
|---|---|
| add a parameter to `int f(int)`, 3 callers (one through a macro) | 3 breaks exact, both modes |
| remove the parameter; one caller is edited by the same patch | 2 breaks exact, 1 probable |
| reorder `(int a, int b)` -> `(int b, int a)` | all callers affected exact, **no** break, "swap arguments" |
| retype `int` -> `long` | callers affected, no break |
| only rename a parameter | no signature effect |
| return `int` -> `void`; callers: stored, `(void)f()`, ignored | stored = break; discarded / ignored = affected |
| change inside the body only, head untouched | no signature effect |
| static `f` with the same name in two files; change one | only that file's callers |
| `f` stored in a handler table, called through the table | address site affected exact; pointer call probable |
| variadic `log(const char *, ...)` gets a fixed parameter | calls with fewer args break |
| C++ overload `g(int)` -> `g(long)` (USR changes), with another `g(double)` | matched by name only when unique; otherwise removed + added |
| field retyped, read through a macro | user affected at the macro line |
| field removed; one user via a typedef'd pointer | breaks exact / probable (git: `missing_members`; patch: users) |
| field renamed `count` -> `n` | removed + added, "maybe renamed" hint, users break |
| field added in the middle; positional global `{1, 2}` | new node; global unknown "by position"; layout note |
| global removed (git mode) | removed node + #25 blind spot, never silent |
| struct field with the same name in another struct | not mixed (Part B rule) |
| patch that does not match the disk | `mismatched`, `summary.not_followed` > 0, no ✅ |
| patch that adds a whole new function | `new` node, not "could not follow" |
| comment-only change in a header | ✅ only if nothing else; not_followed stays 0 |

Plus: the **contract test**: real `check --json` output of three fixture cases passes `ui.schema.check`,
renders with `ui.render_text` and `ui.render_html`, and is byte-identical across two runs; a golden
`tests/golden/check_c_check.txt`. CLI and MCP smoke tests.

## 7. Performance (hook: < 2 s on Redis, patch mode)

Cost of a one-file patch: index freshness check (`fresh_index(want=...)`, no re-parse when the file is
unchanged on disk) + 2 outline parses of that file + a few SQL queries. ADR 0005 measured one outline
parse on Redis "well under a second"; two must fit with the queries.

Plan, in order, measured on Redis 8.10.3 with three patches (`src/t_string.c` signature change,
`src/server.h` field retype, `src/server.c` body-only change), cold and warm:

1. Skip the second parse when no changed line touches a function head or a declaration line (known from
   the index's ranges and the old outline): body-only edits need one parse, or none.
2. Patch mode: parse the disk text with `PARSE_PRECOMPILED_PREAMBLE` and `reparse()` with the patched
   text as an unsaved file, so the headers are parsed once.
3. If still over budget: old side from the index (`functions.signature`, `variables.type`), new side from
   the patched outline only; reorder of same-typed parameters then needs the names from the one parse
   (still exact).

Numbers go into the PR and ADR 0005's addendum. If 2 s is not reachable, report it, don't cut checks.

## 8. Golden facts (hand-checked; proof lines from the pinned source; never from tool output)

New fact kind `check` (`tests/realrepo/facts.py`: `breaks`, `affected`, `unknowns`, `must_not`, plus the
diff fields) run through `check.run` in patch mode; one git-mode fact on a temp git repo of the pinned
tree.

Redis 8.10.3:

1. **Parameter added** to `checkStringLength` (`src/t_string.c:26 static int checkStringLength(client *c,
   long long size, long long append) {`): breaks exact at `src/t_string.c:604`, `623` (setrangeCommand),
   `1391` (appendCommand) (`checkStringLength(c,offset,value_len)`, `checkStringLength(c,stringObjectLen(o),append_len)`).
   **Must not**: any break outside `src/t_string.c` (it is static).
2. **Return type to void**, same function: the three calls compare the result (`!= C_OK`) -> 3 breaks.
3. **Field retype** `src/server.h:2171 long long stat_numcommands;` -> `unsigned long long`: call
   (`src/server.c:4228 server.stat_numcommands++;`), resetServerStats (`2916`), serverCron (`1601`)
   affected exact; **no** breaks; nothing under "could not follow" (the #13 facts, flipped).
4. **Git mode**: fact 1 as a commit in a temp repo: same breaks, from "does not compile" call sites.

SQLite 3.54.1 (`sqlite3.c`):

5. **Field removed** `sqlite3.c:18979 int busyTimeout;`: breaks exact at 147978 (sqlite3Pragma), 190115
   (sqliteDefaultBusyCallback), 190182 (sqlite3_busy_handler), 190235 (sqlite3_busy_timeout). **Must not**:
   190136 (in a non-compiled `#else`).
6. **Field renamed** `busyTimeout` -> `busyTimeoutMs`: removed + added with the rename hint; same 4 breaks.
7. **Field retype** `int busyTimeout;` -> `i64 busyTimeout;`: the same 4 functions affected, no breaks;
   the SQLite #13 fact flips.

Each proof line is re-read from the pinned source during the build; any mismatch is fixed in the plan.

## 9. Files to change

- `src/aerotrace/frontends/cpp/parse.py`: `function_outline` extra fields (outline only).
- `src/aerotrace/index/history.py`: `Outline` fields, `Outliner.text`, preamble reuse.
- `src/aerotrace/toolchain/unidiff.py`: `apply`.
- `src/aerotrace/analysis/diff.py`: `patched` argument, new-side mapping, `added`, patch-mode removed.
- `src/aerotrace/analysis/check.py` (new): comparison, effects, `to_contract`.
- `src/aerotrace/cli.py`: `check` command. `src/aerotrace/mcp/server.py`: `check` tool.
- tests: `tests/fixtures/c_check/`, `tests/test_check.py`, `tests/test_diff.py` (patch new side),
  `tests/test_cli.py`, `tests/test_mcp.py`, `tests/golden/check_c_check.txt`; `tests/realrepo/`
  (`check` kind, harness `patched=`, remove the two #13 `known_bug`s, new facts).
- `docs/decisions/0005-diff-impact.md`: addendum (patch new side, timings). README command list.

## 10. Risks

- **A wrong "safe" from a missed signature change** (macro-generated prototypes, K&R definitions,
  `#ifdef`-different heads). Mitigation: when the head lines changed but the outlines show no difference,
  a change-scope blind spot "the head of f changed but its signature looks the same; check by hand".
- **Outline parse fails or has errors** on one side: the file goes to `unreadable` (counted), signature
  effects for it come from the index side with a blind spot; never "nothing changed".
- **Header outlines use another file's flags** (as today): types under `#ifdef` may differ; stated.
- **Patch-mode breaks are predicted, not compiled.** Wording says "will not compile with the new
  signature (passes 2 arguments, it now takes 3)". Type mismatches are never called breaks.
- **C++ overload matching** by name may pair the wrong overload; only done when unique.
- **Big answers** (`server` global, `redisServer::dirty`): 5,000-node cap and Part B caps.
- **Merge conflicts**: PR #42 (`mcp/server.py`), PR #34 (tests sections). Rebase late, small diff there.
- **Speed**: two parses per file may exceed 2 s on `src/server.c`; section 7.

## 11. Decisions for the owner / coordinator

- **O1. Branch base.** Part C needs #41 and #38 (and #14 from `launch-candidate`). If #38 has not merged
  when the contract test is written, merge `origin/feat/launch-ui` into `feat/check` (one merge commit,
  dropped by the final rebase). Recommended: merge #41 and #38 first.
- **O2. Ids for things not in the index** (new in a patch): `new:{file}:{line}:{name}`, like the removed
  key. The contract only defines `removed:`; this needs a line in the UI plan, no schema change.
- **O3. Same canonical type, other spelling**: users listed `affected` probable (recommended), or left
  out with a note.
- **O4. `diff` patch-mode labels**: the #13 fix plus "removed" / "new" in patch mode slightly change
  `diff --patch` output (fewer false notes, better labels). Recommended: accept as bug fixes.
- **O5. Address sites after a signature change**: `affected` exact (recommended; C compiles it with a
  warning) or `breaks` probable (C++ and newer clang reject it).
- **O6. Exit code 1 when something breaks** (also for probable breaks). Recommended: yes.

Decided (coordinator, 2026-11-09):

- O1: wait for #41 and #38 to merge into `launch-candidate`, then rebase. Do not merge `feat/launch-ui`
  into this branch.
- O2: `new:{file}:{line}:{name}`; the UI builder adds it to the contract.
- O3: yes, probable.
- O4: yes, but each `diff --patch` label fix is its own commit, with its own GitHub issue (earlier-feature
  bug, not fixed silently).
- O5: yes, `affected`.
- O6: yes, exit code 1 for probable breaks too, documented in the help text and README.

## 12. Review (coordinator, 2026-11-09) and what the build does with it

1. **Declaration text changed, outline the same** (bitfield width, `alignas` / packed attributes,
   `volatile` / `_Atomic` placement, array size behind a macro): the outline records a hash of each
   declaration's text (from its extent, initializer excluded). Text changed but type and position equal:
   a change-scope blind spot names it, and its users are listed as **unknown**. Fixtures: bitfield, packed.
2. **Initial value of a global changed** (`int maxclients = 1000` -> `10`, a static const table): the outline
   records a hash of the initializer text; on a change, readers are affected / probable, "its initial value
   changed".
3. **No prototype** (`int f();`, K&R definitions in C): the outline records `has_prototype`. Without one,
   any argument count compiles: callers are affected / probable, never breaks. Fixture.
4. **Prototype and definition no longer match**: each changed file is parsed on both sides anyway, so the
   compile errors of the two parses are compared; a new error ("conflicting types for f") in a function
   is a break of that function (or, for the changed function itself, a change-scope blind spot with the
   error text, so the headline is never an all-clear). C++: a definition whose new signature matches no
   prototype of that name in its translation unit makes its callers breaks / probable ("may not link").
5. **The hook is PreToolUse**: it checks a proposed edit before it is written, so disk is the old side.
   `check --patch` indexes only what is on disk (`fresh_index(want=...)`) and never the patched text. A
   post-edit mode is out of scope.
6. **Patch apply**: exact only (as `_matches_disk` today); line endings kept (CRLF); fixtures for CRLF,
   several hunks, several files. Moving a hunk by its context is not done in this build (such a patch is
   `mismatched`, counted, never mapped wrong); listed as not done.
7. **New files in a patch**: outlined in memory; a new global or function whose name an indexed one already
   has is a break / probable (clash); otherwise the file stays in `not_followed.new_files`.
8. **Fixed limit on every signature or field change**: "Uses in code not compiled with these flags (#if)
   are not checked."
9. **Rename hint may miss**: removed + added stays correct. Tests: file rename + edit in both modes; the
   old path's compile entry is used for both sides.
10. **Speed**: measured first; an outline cache under `.aerotrace/` keyed by file hash is added only if the
   numbers need it (called out as extra stored data); `SKIP_FUNCTION_BODIES` tried for the new side.
11. "static added" breaks callers in other files; `int -> pointer` return compared with `!= C_OK` stays
   affected in C; C++ default argument removed; O5 by language: address sites are breaks / probable in
   C++, affected in C.
12. Must-not facts: bitfield width change -> unknown, never an all-clear; a call to a `()` function -> no
   break.

Coordinator: build locally on `feat/check`; no push, no PR (the owner pushes).

Review of #48 (coordinator): O5 changed. In C too, a function stored or passed as a pointer whose type no
longer matches is a probable break (clang 16+ and GCC 14+ reject it), not "affected".
