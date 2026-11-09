# Plan: the Claude Code guardrail hook (item 7)

Status: plan, waiting for review. Builds on `aerotrace check` (PR #48) and the test map (PR #34).

## Goal

Before Claude Code edits a C/C++ file, AeroTrace checks the proposed edit and tells the agent what it
would break. It warns by default and only blocks when the repo's own rules say so. It never blocks
because AeroTrace itself failed.

## 1. Input format

Claude Code runs a `PreToolUse` hook command with one JSON object on stdin:

```json
{"session_id": "...", "transcript_path": "...", "cwd": "/repo", "hook_event_name": "PreToolUse",
 "tool_name": "Edit", "tool_input": {"file_path": "/repo/src/a.c", "old_string": "...",
 "new_string": "...", "replace_all": false}}
```

- `Edit`: `file_path`, `old_string`, `new_string`, `replace_all`.
- `MultiEdit`: `file_path`, `edits: [{old_string, new_string, replace_all}]`, applied in order.
- `Write`: `file_path`, `content` (new file or full overwrite).

Any other tool name, a missing field, or JSON we can't read: exit 0 with no output (silent allow).
The exact field names get checked against the installed Claude Code (2.1.319) by one tiny experiment
(a hook that dumps stdin) before the build, and the unit-test sample inputs are copied from it.

Output: exit 0 and one JSON object on stdout:

- warn: `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "<text>"}}`.
  No `permissionDecision`, on purpose: sending `"allow"` would skip the user's own permission prompt.
- block: `"permissionDecision": "deny"` and `"permissionDecisionReason": "<text>"` (shown to the agent).
- silent: no output.

If the experiment shows `additionalContext` does not reach the model for `PreToolUse` in this version,
the fallback is `systemMessage` + `permissionDecisionReason` with no decision; whichever is used is
checked by the end-to-end test.

## 2. Patch construction

1. `file_path` must be inside the repo root (found from `cwd` like the CLI does) and have a C/C++
   suffix the indexer knows; else silent.
2. Old text = the file on disk (missing file = empty, the `Write` new-file case).
3. New text = old text with the edits applied exactly like Claude Code does: `old_string` must occur
   (once, unless `replace_all`); if it doesn't, the tool call will fail anyway, so silent.
4. `difflib.unified_diff` with `a/<rel>` / `b/<rel>` headers (`/dev/null` for a new file), passed to
   `check.check_change(root, open_index, patch_text=...)` in-process. No subprocess.

Nothing is written anywhere except `.aerotrace/` (hook state, optional log).

## 3. Risk levels (from the `aerotrace-check` summary)

| level | when | what the agent sees |
|---|---|---|
| high | `breaks_exact > 0`, or a rule threshold says high | full list (capped at 20 lines, "and N more"): broken / affected functions with file:line and why, data affected, tests to run (`testimpact.for_diff`, only when the index has test entries; skipped when over the time budget, and then said so) |
| medium | `breaks_probable > 0`, or `affected_direct + affected_further >= 10` (default, configurable) | 2-3 lines: counts plus the top 3 names, and "run `aerotrace check` for the list" |
| low | none of the above | silent |

Statuses `no_index` / `index_error` / timeout are not levels: they give the "couldn't check" note
(section 6). `nothing_to_check` is silent. Blind spots that matter (e.g. "not followed" lines inside the
edit) add one line at medium/high; we never say "safe".

## 4. `.aerotrace/rules.toml`

```toml
[thresholds]            # optional; defaults shown
medium_affected = 10    # affected functions that make an edit medium
timeout_seconds = 5     # our own budget per hook call

[[rule]]
name = "core is protected"
paths = ["src/server.c", "src/*.h"]   # globs, repo-relative, matched with fnmatch on POSIX paths
level = "medium"                     # min level at which this rule fires: low | medium | high
action = "block"                     # warn | block (default warn)
```

- No file: defaults, everything warns, nothing blocks.
- A rule fires when the edited file matches `paths` (all files if `paths` is absent) and the edit's
  level is at least `level`. If any firing rule says `block`, the edit is denied with the high-level
  list plus "blocked by rule '<name>' in .aerotrace/rules.toml".
- `level = "low"` + `block` = protected path: any edit to it is denied, even with no effects found.
  A protected path (`level = "low"` + `block`) is blocked even when the check could not run (no index,
  timeout, error): protecting a path does not depend on the index. Rules with a higher level never block
  a check that could not run (changed after review).
- Invalid TOML, unknown key, wrong type, bad action: the whole file is ignored, defaults used, and the
  agent gets one line "AeroTrace ignored .aerotrace/rules.toml: <reason>". Never block.

## 5. Install, on/off

- `aerotrace install --client claude` writes `.mcp.json` as today, and also merges into
  `<repo>/.claude/settings.json`:
  `hooks.PreToolUse += {"matcher": "Edit|MultiEdit|Write", "hooks": [{"type": "command",
  "command": "<launch command> hook run", "timeout": 30}]}`.
  Merge keeps every other key and hook; an existing AeroTrace entry (found by `hook run` in the
  command) is replaced, not duplicated. Unreadable settings.json: stop with an error, write nothing
  (same as `.mcp.json` today). `--client cursor/codex`: unchanged.
- `aerotrace hook off` / `on`: writes / removes `.aerotrace/hook-off`. `hook run` exits silently when
  the file exists. This avoids rewriting the user's settings and is instant. `aerotrace hook status`
  prints whether it is installed and on.
- `aerotrace hook run` is the command Claude Code calls (reads stdin, prints the JSON).

## 6. Failure behaviour (never block on our own failure)

- Index missing: do not build one (it can take minutes); note "AeroTrace couldn't check this edit (no
  index; run `aerotrace index`)".
- Index stale: `index_plan(want=[file])`; a small refresh (at most 20 files to re-parse) runs inside
  the budget, so the agent's own earlier edits are picked up; anything bigger: note "(index is out of
  date for N files; run `aerotrace index`)".
- `index_error`, any exception, or over `timeout_seconds`: allow, one short note with the reason. The
  check runs in a worker thread; at the deadline the hook prints the note and exits (`os._exit(0)`).
  The 30 s `timeout` in settings.json is only a backstop.
- The note starts "AeroTrace couldn't check this edit (...)" so it is never confused with "no effect".

## 7. Timing plan (< 2 s on Redis 8.10.3, one-function edit)

Measure `hook run` wall time (process start to exit, warm index, pinned Redis from the realrepo cache)
for: a body-only edit in `src/t_string.c`, a signature change in `src/t_string.c`, a field retype in
`src/server.h`. Report min / median of 5 runs, plus Python start + import time separately. If over
2 s: lazy imports first, then the check plan's section 7 steps. If still over, report, don't cut checks.

## 8. Tests

Unit (`tests/test_hook.py`, sample JSON inputs on a tiny fixture repo):
Edit (breaking signature change -> high text with function and test names), MultiEdit, Write of a new
file, non-C file -> silent, unknown tool -> silent, `old_string` not found -> silent, body-only edit ->
silent, missing index -> allow + note, broken index file -> allow + note, block rule -> deny, protected
path -> deny, invalid rules.toml -> warn line and no block, timeout (monkeypatched slow check) ->
allow + note, hook-off file -> silent, install merge of settings.json (existing keys and hooks kept, no
duplicate on re-run), output is valid JSON and sorted/deterministic.

End-to-end (`@pytest.mark.e2e`, skipped by default): copy the tiny C fixture repo to a temp dir, index
it, install the hook, run one `claude -p "<change add() to take 3 args>" --output-format stream-json
--verbose --max-turns 3 --max-budget-usd 1` with the inherited environment, assert the AeroTrace
warning text appears in the stream. One run; its output goes into the PR.

## 9. Files

New: `src/aerotrace/analysis/rules.py` (rules.toml, risk levels, the warning text) and
`src/aerotrace/hooks/claude.py` (thin: hook JSON in, patch, deadline, JSON out), `tests/test_hook.py`,
`tests/test_hook_e2e.py`. Changed: `cli.py` (`hook run|on|off|status`), `mcp/install.py` (settings.json
merge), `pyproject.toml` (`e2e` marker, excluded by default), README / docs for the hook.
No index change; the only new file under `.aerotrace/` is `hook-off` (and `rules.toml`, written by the
user).

## 10. Open questions for the coordinator

1. Install the hook by default with `install --client claude`, or behind `--hook`? Plan: by default
   (the item says "also sets up the hook").
2. Small-refresh limit of 20 files inside the hook: OK?

## 11. Review (coordinator) and build results

Decisions: install adds the hook by default (said in its output and the README); small refresh up to 20
files, beyond that allow + "index is stale (N files changed); run `aerotrace index`".

- Input format checked with a dump hook on Claude Code 2.1.319: fields as in section 1 (plus
  `prompt_id`, `permission_mode`, `tool_use_id`). `additionalContext` reaches the model as
  "PreToolUse:Edit hook additional context: ..." (no fallback needed). stream-json does not repeat it;
  the session transcript (`~/.claude/projects/<dir>/<session>.jsonl`, an `attachment` entry) has it, so
  the e2e test reads the transcript.
- Code: `analysis/rules.py` (rules, levels, text), `hooks/claude.py` (hook layer), `cli.py`
  (`aerotrace hook on|off|status|run`), `mcp/install.py` (`hook_plan`).
- Timing, `aerotrace hook run` as a process, warm index, Redis 8.10.3, 5 runs each (min / median):
  body-only edit of `getCommand` 0.95 / 0.96 s; signature change of `getGenericCommand` 1.12 / 1.12 s;
  `client.argc` int -> long in `server.h` 1.41 / 1.42 s. Python start + import: 0.05 s. All under 2 s.

## 12. Changes after review of #50

1. CRLF: the hook reads the file with `newline=""`, so the patch matches the disk byte for byte; an
   `old_string` written with plain newlines also matches CRLF lines. `check`'s own CRLF bug: #51.
2. A protected path (`level = "low"` + `block`) blocks even when the check can't run.
3. Install treats only its own exact command as AeroTrace's hook; a user hook that looks alike is kept.
4. Timeout during a small refresh: `store.write_index` writes a temporary file and renames it into place
   (`os.replace`), and parse-cache entries are written the same way, so exiting mid-refresh can't leave
   a half-written index (at most a stray temporary file under `.aerotrace/`).
