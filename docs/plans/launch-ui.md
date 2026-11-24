# Plan: launch UI (work item 9)

Status: built (revised after review; see section 9).
Branch: `feat/launch-ui` (from `origin/launch-candidate`).

What this item builds:

1. A terminal renderer for `aerotrace check`. The command itself, and the analysis that fills the JSON,
   come in item 5 Part C. This PR only adds functions Part C calls.
2. An HTML renderer for `aerotrace check --html <out.html>`: one self-contained, offline HTML file.
3. `docs/launch/README.draft.md`. README.md itself is not touched.
4. Later, not now: screenshots of a real Redis change in `docs/launch/screenshots/`.

Tooling note: no "Open UI" MCP tools were available. The HTML, CSS and JS are hand-written: no framework,
no build step, no new runtime dependency.

Code:

| file                                   | what it does                                                       |
|----------------------------------------|--------------------------------------------------------------------|
| `src/aerotrace/ui/schema.py`           | the versioned contract, `check(data)`, `node_word()`               |
| `src/aerotrace/ui/terminal.py`         | `render(data, color, unicode, limit)`, `style_for(stream, env)`    |
| `src/aerotrace/ui/html.py`             | `render(data) -> str`, `write_html(data, out, root) -> message`    |
| `src/aerotrace/ui/assets/report.*`     | the HTML skeleton, CSS and JS that get inlined                     |
| `tests/ui/samples.py`                  | sample data, hostile data, and `big(n)` graphs for size and speed  |
| `tests/fixtures/ui/check-v1-sample.json` | the contract file Part C tests its output against                |

## 0. Rules this keeps

- **No analysis logic in the renderer.** It never decides what breaks, what is affected, or how sure a
  link is. Part C puts every status, certainty, reason and count into the JSON. The renderer checks the
  shape, picks wording from counts, caps terminal lists, and lays out the graph.
- **Deterministic.** Same JSON in, same bytes out (text and HTML). No timestamps, no machine paths, no
  random numbers, no hash-order iteration.
- **Fully local.** The HTML makes no network request. A Content-Security-Policy with sha256 hashes stops
  the browser from loading or running anything else, even if there were a bug.
- **A wrong "safe" is the worst bug.** "✅" is only shown when nothing breaks, nothing is affected or
  unknown, and no part of the change was skipped. Grey nodes say "NO EFFECT FOUND", never "safe".
  `check()` refuses data whose counts are more hopeful than its lists.
- **Product rule 7** (no web UI) has an exception for this: the owner asked for `--html` (a static,
  offline file). The coordinator will ask the owner to note it; CLAUDE.md is not changed here.

## 1. Input: the `aerotrace-check` JSON, version 1

Part C produces this; both renderers read it. `aerotrace check --json` should print exactly this object.
The full example is `tests/fixtures/ui/check-v1-sample.json`.

```jsonc
{
  "format": "aerotrace-check",       // fixed
  "version": 1,                       // renderers refuse other versions, with a clear message
  "tool": "aerotrace 0.1.0",          // tool version, no build date
  "status": "ok",                     // "ok" | "no_index" | "index_error" | "nothing_to_check"
  "message": null,                    // required for no_index and index_error: what to do next

  "change": {                         // may be null for no_index / index_error
    "mode": "git", "base": "HEAD",    // "git" | "patch"; base is null in patch mode
    "files": 2, "other_files": 1,     // C/C++ files touched; other files in the diff (ignored)
    "changed_functions": 1, "new_functions": 0, "removed_functions": 1, "changed_declarations": 1,
    "not_followed": {                 // straight from DiffImpact; parts of the change AeroTrace skipped
      "outside": [{"file": "src/t_string.c", "lines": [40, 41], "other_side_lines": []}],
      "not_indexed": [], "deleted": [], "mismatched": [], "unreadable": [], "new_files": []
    }
  },

  "summary": {                        // headline numbers; the renderer prints them, never recomputes
    "breaks_exact": 3,                // will not build or link, link exact
    "breaks_probable": 1,             // will not build or link, link probable
    "affected_direct": 2,             // affected, distance 1
    "affected_further": 4,            // affected, distance > 1
    "unknown": 2,
    "no_effect": 1,
    "not_followed": 2                 // entries in change.not_followed + blind spots with scope "change"
  },

  "nodes": [                          // sorted by (status, certainty, distance, file, line, id)
    {
      "id": "fn:src/t_string.c:getCommand",       // the index's function id, or a variable id
      "name": "getCommand", "kind": "function",   // "function" | "field" | "global" | "static_local"
      "file": "src/t_string.c", "line": 398,
      "status": "breaks",            // "changed" | "new" | "removed" | "breaks" | "affected" | "unknown" | "no_effect"
      "certainty": "exact",          // "exact" | "probable" | "unknown"
      "link": "exact",               // raw label from graph.CONFIDENCE_ORDER, or null for changed nodes
      "why": "passes 1 argument; getGenericCommand now takes 2",
      "distance": 1,                 // 0 for changed/new/removed, and only for them
      "via": "fn:src/t_string.c:getGenericCommand",  // id one step closer to the change, or null
      "via_name": "getGenericCommand",               // display name of that step (kept when via is null)
      "lines": [399]                 // lines of the link in this node's file
    }
  ],

  "edges": [                          // sorted; from -> to in code direction (caller -> callee, fn -> data)
    {"from": "fn:src/t_string.c:getCommand", "to": "fn:src/t_string.c:getGenericCommand",
     "kind": "calls", "certainty": "exact"}      // kind: "calls" | "reads" | "writes"
  ],

  "sections": {                       // what the lists show, in order; values are node ids
    "functions": ["..."], "data": ["..."],
    "tests_to_run": [{"name": "tests/unit/type/string.tcl", "covers": ["<id>"], "why": "..."}],  // or null
    "untested_changes": [{"node": "<id>", "why": "no test reaches it"}],                         // or null
    "blind_spots": [{"text": "...", "scope": "change", "node": null}]   // scope: "change" | "index"
  },

  "omitted": {"nodes": 0},            // Part C caps the graph at 5,000 nodes and says how many it dropped

  "proof": {"index_files": 117, "index_functions": 5064, "depth": 5, "stopped_early": false}
}
```

### 1.1 Two questions, two fields

- `certainty` answers **"is the link real?"**: `exact` = the compiler resolved every step from this node to
  the change; `probable` = at least one step is a function pointer, virtual call or same-name match;
  `unknown` = the path leaves code AeroTrace can follow. It comes from `analysis/certainty.py`.
- `status` answers **"what does the change do to it?"**: `breaks` = it will not build or link (and nothing
  else, not "behaves differently"); `affected` = it uses changed code or data, so its behaviour may
  change; `unknown` = the effect could not be followed; `no_effect` = checked and no effect found
  (orientation only, for example a callee of a changed function); `changed` / `new` / `removed` = part
  of the change.
- `link` keeps the raw label (`exact`, `calls_removed`, `uses_changed`, `via_pointer`, ...) for the details
  pane.

### 1.2 Rules for Part C (checked by `schema.check`)

- **Only Part C sets `breaks`.** Today's sources: `calls_removed` (matched by bare name in
  `diff.py` `_seeds` / `_still_calling`) and `signature.Site.broken`. Either can be `probable` (a same-name
  match), so a break is `exact` or `probable`, **never `unknown`** (rejected).
- `status: "unknown"` and `certainty: "unknown"` always go together (rejected otherwise).
- Changed, new and removed nodes have distance 0, and only they do.
- **Ids.** Node ids are the index ids. A removed function uses the key `removed:{file}:{line}:{name}`
  from `diff.py`; a function that is new in a patch not applied yet uses `new:{file}:{line}:{name}`. `Hit.via` is a qualified name, not an id: Part C maps it to an id. If a name maps to
  more than one id, Part C sets `via` to null and keeps `via_name`. Under the 5,000-node cap, edges and
  `via` pointing at dropped nodes are removed (`via_name` stays). Every id in `via`, `edges` and
  `sections` must exist, or `check` stops.
- **Counts are explicit** and can't be more hopeful than the lists: for each status, `summary` must be
  at least what `nodes` shows, and exactly that when `omitted.nodes` is 0 (so "can break 5" with 3
  listed is refused). An all-zero summary is refused when nodes were left out or the walk stopped early.
  If `change.not_followed` lists anything, or a blind spot has scope
  `change`, then `summary.not_followed` must be > 0 ("a wrong all-clear").
- Blind spots have a `scope`: `change` ones (about this diff) block the all-clear; `index` ones (global
  limits like "macros are not followed") are always shown but don't block it forever.
- Every node that is not part of the change is in exactly one of `sections.functions` /
  `sections.data`; changed, new and removed nodes are in neither (they are listed under "What changed").
- Non-ok statuses are checked too: `nothing_to_check` needs `change`, and lists what it skipped
  (`not_followed`), so a diff of unindexed files is never an all-clear. Non-ok data can't list nodes.
- Tests sections may be `null` when there is no test map yet. Then the output says "Tests: not known yet",
  never "Tests to run (0)".
- Part C should add its own test: its output for a fixture passes `ui.schema.check` and renders.

## 2. Terminal output

`render(data, *, color=False, unicode=True, limit=8)` is pure. `style_for(stream, env)` picks the mode:

| situation                                  | colour | icons   |
|--------------------------------------------|--------|---------|
| TTY, UTF-8                                 | yes    | Unicode |
| not a TTY (pipe, file, CI log), UTF-8      | no     | Unicode |
| `NO_COLOR` set and not empty               | no     | Unicode (NO_COLOR is about colour only) |
| stream encoding is not UTF-8               | (TTY)  | ASCII   |
| `TERM=dumb`                                | no     | ASCII   |

Icons, always next to a word:

| meaning         | Unicode | ASCII  | word              | colour (TTY only)   |
|-----------------|---------|--------|-------------------|---------------------|
| headline, risk  | ⚠️      | `[!]`  | -                 | bold                |
| headline, clear | ✅      | `[ok]` | -                 | bold                |
| headline, info  | ℹ️      | `[i]`  | -                 | bold                |
| breaks          | ✖       | `[x]`  | BREAKS / MAY BREAK| bold red (1;31)     |
| affected        | ▲       | `[~]`  | AFFECTED          | bold yellow (1;33)  |
| unknown         | ?       | `[?]`  | UNKNOWN           | bold                |
| no effect       | ·       | `[-]`  | NO EFFECT FOUND   | none                |
| no test         | ○       | `[ ]`  | NO TEST           | none                |

Only bold, red and yellow, only on the icon and the word (works on light and dark terminals). No dim,
grey or background colours. Lines are wrapped at word boundaries only on a TTY, to the terminal width,
with continuation lines under the text (`render(..., width=N)`; a single long word such as a path is
never split). Pipes, CI logs, `--json`, MCP answers and the goldens are not wrapped, so they stay the
same everywhere and are easy to grep. In data strings, control characters, bidi controls
(U+061C, U+200E/F, U+202A-202E, U+2066-2069) and U+2028/2029 are shown as `\uXXXX` (terminal and HTML),
so a name can't send escape codes or reorder text. In ASCII mode every non-ASCII character is escaped
too (`backslashreplace`), so a non-UTF-8 stream never raises UnicodeEncodeError. Numbers use `1,234`.
A short "What changed" section names the changed, new and removed functions. Under a ✅, the index-wide
limits are printed right below the headline ("Index limit: ..."), so a pass never hides them.

### 2.1 Headline (from counts only)

| summary                                         | first line                                                                       |
|-------------------------------------------------|----------------------------------------------------------------------------------|
| breaks_exact > 0                                | `⚠️  This change can break N things you didn't touch (N BREAKS, M AFFECTED).`     |
| only breaks_probable > 0                        | `⚠️  N things you didn't touch may not build or link (N MAY BREAK, M AFFECTED).`  |
| affected > 0, no breaks                         | `⚠️  This change may affect N things you didn't touch (D direct, F further up). None is sure to break.` |
| only unknown > 0                                | `⚠️  N things this change reaches are unknown: AeroTrace could not follow them. Check by hand.` |
| only not_followed > 0                           | `ℹ️  Nothing found that this change can break, but N parts of the change could not be followed.` |
| everything 0                                    | `✅ Nothing found that this change can break, in the code AeroTrace can see.`     |

The second line lists the other non-zero counts, direct counts first ("1 more may not build or link, 6
may be affected (2 direct, 4 further up), 2 are unknown and 2 parts of the change could not be
followed."). When something breaks, the first line ends with the counts in the words the lists use
(`BREAKS`, `MAY BREAK`, `AFFECTED`, `UNKNOWN`; zero counts left out), so the verdict is complete on one
line. Before the proof line comes the coverage line, from `proof.build_files*` (only when the JSON has
them): `Checked 117 of 117 files in this build (1 had parse errors).` If some repo sources are not in
compile_commands.json, one short line follows and names up to 3 of them: `1,696 C/C++ files outside this
build were not checked: deps/a.c, deps/b.c, deps/c.c, ... (--all lists them).` (the hint is the CLI's;
MCP and HTML name the same 3). Under a ✅ or ℹ️ "nothing found" headline this line comes right below the
headline as an "Index limit:", not at the end, because a caller in those files is exactly what a pass
can miss. This line replaces the long "not in compile_commands.json" index limit in `check`, so what was
not seen is still always said.
`aerotrace check --all` removes the caps and lists those files. The proof line at the end says only what
is true about the run:
`index: 117 files, 5,064 functions; followed callers up to 5 steps (stopped early: no)`.

### 2.2 Mock-ups (these are the goldens in `tests/golden/ui/`)

Names are Redis-flavoured but made up: display test data, not facts about Redis.

Normal, ASCII (`sample.ascii.txt`, for non-UTF-8 streams; `sample.txt` differs only in the icons;
this block is from before review #38 and now also has a "What changed" section after the headline):

```text
[!] This change can break 3 things you didn't touch.
    1 more may not build or link, 6 may be affected (2 direct, 4 further up), 2 are unknown and 2 parts of the change could not be followed.
    You changed 1 function, removed 1 function and changed 1 declaration in 2 C/C++ files (compared with HEAD).
    (1 other file in the diff is not C or C++.)

Functions (11)
  [x] BREAKS     getCommand  src/t_string.c:398
                 passes 1 argument; getGenericCommand now takes 2. Link: exact.
  [x] BREAKS     setrangeCommand  src/t_string.c:612
                 still calls checkStringLength, which this change removes. Link: exact.
  [x] BREAKS     appendCommand  src/t_string.c:688
                 still calls checkStringLength, which this change removes. Link: exact.
  [x] MAY BREAK  getexCommand  src/t_string.c:421
                 calls a getGenericCommand (2 functions have that name); if it is this one, it passes 1 argument and it now takes 2. Link: probable.
  [~] AFFECTED   _addReplyToBuffer  src/networking.c:420
                 reads and writes client.bufpos, whose declaration changed. Link: exact.
  [~] AFFECTED   _addReplyProtoToList  src/networking.c:460
                 calls _addReplyToBuffer. Link: exact.
  [~] AFFECTED   getdelCommand  src/t_string.c:470
                 calls a function pointer of the same type as getGenericCommand. Link: probable.
  [~] AFFECTED   call  src/server.c:3780
                 calls getCommand through c->cmd->proc, a function pointer. Link: probable.
  ... and 3 more (1 AFFECTED, 1 NO EFFECT FOUND, 1 UNKNOWN). Raise --limit or use --html to see all.

Data (2)
  [~] AFFECTED   server.stat_net_output_bytes (global)  src/server.h:1890
                 _addReplyToBuffer writes it; 4 functions read it. Link: probable.
  [?] UNKNOWN    client.reply (field)  src/server.h:1205
                 _addReplyToBuffer passes it to listAddNodeTail as void *; what happens next is not known. Link: unknown.

Changes AeroTrace could not follow (1)
  [?] src/t_string.c: lines 40-44 outside any function.

Tests to run (2)
  tests/unit/type/incr.tcl    covers getCommand
  tests/unit/type/string.tcl  covers appendCommand, getCommand, setrangeCommand

Untested changes (1)
  [ ] NO TEST    getGenericCommand  src/t_string.c:380
                 no test reaches it

Blind spots in this change (1)
  [?] 3 calls through function pointers in src/t_string.c were matched by type only.

Limits of the index (1)
  [?] Macro uses are not followed anywhere in the index.

Link: probable = the link is real, but may not apply here (pointer, virtual call, same name, other object); unknown = the data leaves code AeroTrace can follow; what happens next is not known.

index: 117 files, 5,064 functions; followed callers up to 5 steps (stopped early: no)
```

Nothing affected (`nothing.txt`):

```text
✅ Nothing found that this change can break, in the code AeroTrace can see.
    You changed 1 function in 1 C/C++ file (compared with HEAD).
    No other indexed function calls what changed or reads what it writes.

Tests to run (1)
  tests/unit/type/string.tcl  covers getGenericCommand

Limits of the index (1)
  ? Macro uses are not followed anywhere in the index.

index: 117 files, 5,064 functions; followed callers up to 5 steps (stopped early: no)
```

Nothing found, but part of the change was skipped (`unclear.txt`), never ✅:

```text
ℹ️  Nothing found that this change can break, but 2 parts of the change could not be followed.
    You changed 1 function in 1 C/C++ file (compared with HEAD).

Changes AeroTrace could not follow (2)
  ? src/t_string.c: lines 40-42 outside any function.
  ? src/extra.c is not in the index (not in compile_commands.json, excluded, or a header no indexed file includes).

Tests: not known yet (no test map in the index).

Limits of the index (1)
  ? Macro uses are not followed anywhere in the index.

index: 117 files, 5,064 functions; followed callers up to 5 steps (stopped early: no)
```

Index missing (`no_index.ascii.txt`), never a pass in any mode:

```text
[!] AeroTrace has no index for this repo yet, so it checked nothing.
    Run `aerotrace index` first (Redis takes about a minute), then run `aerotrace check` again.
```

Also golden: `index_error`, `nothing_to_check` (ℹ️ when other files changed, since build files can matter),
`affected_only`, and `sample.ansi.txt` (colour).

Exit codes are Part C's call. Suggestion: 0 = nothing breaks, 1 = something breaks, 2 = could not check.
`no_index` must never exit 0.

## 3. HTML report

### 3.1 Layout

```text
+--------------------------------------------------------------------------------------+
| ⚠ This change can break 3 things you didn't touch.                                   |
| 1 more may not build or link, 6 may be affected (2 direct, 4 further up), ...        |
| index: 117 files, 5,064 functions; followed callers up to 5 steps (stopped early: no)|
| Search [name, file or file:line]  [Theme: auto]                                      |
| Showing 13 of 16 nodes. 3 are folded into "+N more" bubbles ...      (aria-live)    |
+---------------------------+------------------------------------------+---------------+
| LIST (main path for       | [Expand all] [Focus] [Fit] [+] [-]       | DETAILS       |
|  keyboard/screen reader)  |                                          | getCommand    |
| > What changed (3)        |          GRAPH (SVG)                     | ✖ BREAKS      |
| > Functions (11)          |    changed nodes in the centre,          | Where  [copy] |
| > Data (2)                |    rings by distance                     | Why           |
| > Changes not followed    |                                          | How sure      |
| > Tests / Untested        |                                          | Path back     |
| > Blind spots / Limits    | Legend: shape + icon + word              | Lines         |
+---------------------------+------------------------------------------+---------------+
```

- The header and the lists are rendered in Python, so every fact is readable without JavaScript.
- Every Python-inserted string goes through `html.escape(quote=True)`; JS only uses `textContent` and
  attributes (never `innerHTML`).
- No graph (only header and message) for `no_index`, `index_error`, `nothing_to_check` and 0 nodes; all
  have goldens. `no_index` / `index_error` never show ✅.

### 3.2 Status: colour + shape + icon + word

| status            | light / dark colour    | shape         | icon | word             |
|-------------------|------------------------|---------------|------|------------------|
| changed/new/removed | blue #1d4ed8 / #84adff | diamond (removed: dashed, hollow) | square / + / x | CHANGED / NEW / REMOVED |
| breaks            | red #b42318 / #f97066  | square        | x    | BREAKS / MAY BREAK |
| affected          | amber #b54708 / #fdb022| triangle      | !    | AFFECTED         |
| unknown           | hollow, dashed outline | circle        | ?    | UNKNOWN          |
| no effect         | grey #667085 / #98a2b3 | small circle  | -    | NO EFFECT FOUND  |

The table lives once in Python (`html.SHAPES` + `schema.node_word`) and is passed to the JS in the data
block; the JS draws nodes only from it. Shapes and icons are SVG paths (no font needed). Each node has
an `aria-label` and `<title>` like "BREAKS: getCommand, src/t_string.c line 398. Link: exact."

### 3.3 Graph without libraries

- **Radial rings by distance.** Ring 0 (centre) holds the changed nodes. Ring k holds nodes at
  distance k, ordered by the angle of their `via` parent, then status, then input order, so children sit
  near their parent with no force simulation. Ring radius grows with the number of nodes. Fully
  deterministic.
- **All ≤5,000 nodes are drawn once** (no viewport culling). Showing and hiding is one class per node.
- Edges are stored as `[i, j, kind, certainty]` indexes and drawn as **three `<path>` elements** (calls,
  data, highlight), so 15,000 edges are 3 DOM nodes. `vector-effect: non-scaling-stroke` keeps them thin.
- Pan (drag), zoom (wheel, +/-), fit. Labels: one class on the SVG (`lod`) turns all labels on when
  zoomed in or when 60 or fewer nodes are shown; changed, breaks, selected and matched nodes are always
  labelled.

### 3.4 Collapsed by default, search, focus

- **Collapsed:** ring 0 and the first 40 of ring 1 are shown. **BREAKS and UNKNOWN nodes are always
  shown, at any distance, with their `via` chain back to the change.** The rest of each ring is a
  bubble that says what is inside: "+12 more at distance 2: 10 affected, 2 no effect found". Click it
  or press E to open it; "Expand all" opens everything.
- **Search** (`/`): name, file or `file:line`. Matches are shown (with their chain) and highlighted,
  others faded. The list filters to matches and says how many are hidden. Enter selects the first match.
- **Focus** (button or F): only the selected node, its chain back to the change, and its neighbours (up
  to 200). The status line says "Focus: getCommand (3 of 16 nodes shown). Press Escape to show all."
  and the list says what it hides.
- **Details:** name, status word, `file:line` with a copy button (clipboard API; falls back to selecting
  the text on file:// pages), kind, why, how sure (with the meaning from `certainty.MEANING`), raw link
  label, distance, path back to the change (each step clickable; a dropped step shows its name and
  "left out of this report"), link lines.

### 3.5 Keyboard and screen readers

- The list (buttons in `<details>` sections) is the main path for screen readers: every fact is there.
- The graph is **one** tab stop: `role="application"` with `aria-activedescendant` on the current node
  and an `aria-live` line that reads the node. Arrow keys move to the nearest visible node in that
  direction, Home goes to the change, F focus, E expand, Escape back to all, +/-/0 zoom and fit.
- Visible focus outline in both themes; `prefers-reduced-motion` respected.

### 3.6 Light and dark

CSS variables with light values, `prefers-color-scheme: dark` values, and a toggle (auto / light / dark)
that sets `data-theme` and remembers it in `localStorage` (inside `try`; nothing is written to the file).

### 3.7 Self-contained, offline, deterministic

- `<meta charset>` first, then the CSP:
  `default-src 'none'; script-src 'sha256-…'; style-src 'sha256-…'; base-uri 'none'; form-action 'none'`.
  The hashes are computed in `html.py` from the inlined JS and CSS (deterministic). No `unsafe-inline`,
  no `img-src`, no inline `style=` attributes. Also `<meta name="referrer" content="no-referrer">`.
- Data block: `<script type="application/json">` with sorted keys, ASCII only, `<`, `>`, `&` written as
  JSON unicode escapes (backslash-u003c and so on), and every `/` next to another `/` written as `\/`, so the file never contains `//`
  outside the one allowed string.
- The only URL-like string is the SVG namespace `http://www.w3.org/2000/svg` in `report.js` (an
  identifier for `createElementNS`, not a request). Inline SVG in the markup has no `xmlns`/`xlink`.
  `report.js` has no `//` comments, so the scan stays simple.
- `write_html(data, out, root)` refuses to write inside `<root>/.aerotrace/` or onto a folder, and its
  message says when it replaced an existing file.

## 4. Performance

Measured in headless Chrome (Playwright, `tests/ui/test_ui_browser.py`) on `samples.big(n)` (random
edges, so this is a worst case for crossings):

| data                          | file size | first render | expand all | search (Enter) | arrow key |
|-------------------------------|-----------|--------------|------------|----------------|-----------|
| 1,000 nodes / ~3,000 edges    | 428 KB    | 22-37 ms     | 11-31 ms   | 12 ms          | -         |
| 5,000 nodes / ~15,000 edges   | 2.0 MB    | 61-65 ms     | 32-41 ms   | 65 ms          | 46 ms     |

"First render" is from script start to the first frame (`data-render-ms`), so it includes JSON parsing.
Python renders 5,000 nodes in about 50 ms. Size budget (tested in plain pytest): ≤ 500 KB at 1,000 nodes,
≤ 2.5 MB at 5,000 (the review suggested about 2 MB; 5,000 lands at 2.0 MB, and the budget leaves room for
longer real names and reasons).

## 5. Tests

Plain pytest (default run, no browser, no network): 71 tests in `tests/ui/`.

1. **Terminal goldens** for 7 cases in Unicode and ASCII, plus a colour golden. ASCII output is checked to
   be pure ASCII.
2. **Mode picking**: `style_for` for TTY, pipe, `NO_COLOR=1`, `NO_COLOR=""` (does not count), latin-1,
   ascii, `TERM=dumb`, unknown codec.
3. **No colour-only meaning**: colour output with escapes stripped equals the plain output; every item
   has a status word.
4. **Headline rules**: only exact breaks count in "can break N"; probable-only breaks say "may not build
   or link"; direct counts first; skipped parts give ℹ️ never ✅; no_index never looks like a pass;
   capped lists say what they left out; control characters are replaced.
5. **Contract**: wrong format/version, break with certainty unknown, unknown status without unknown
   certainty, missing ids in sections/edges/via, duplicate ids, wrong distances, summary lower than
   nodes, skipped parts without a count, missing scope, missing message. Each is refused by `check`,
   and by both renderers.
6. **HTML golden snapshots** for 7 cases (byte for byte; `UPDATE_GOLDEN=1` rewrites them).
7. **Determinism**: twice in-process, with dict keys in reverse order, and in subprocesses with
   `PYTHONHASHSEED` 0, 1 and 12345.
8. **No external URL**: the output has exactly one copy of the SVG namespace and, apart from it (and the
   CSP hashes), no `http(s):`, `data:`, `javascript:`, `//`, `url(`, `@import`, `<link`, `<img`, `src=`,
   `xlink`, `fetch(`, `XMLHttpRequest`, `WebSocket`, `sendBeacon`, `navigator.` (except
   `navigator.clipboard`), `innerHTML`, `eval(` and similar; every `href` starts with `#`. The assets have
   no `//`.
9. **CSP**: charset first, CSP second, hashes match the inlined script and style, no `unsafe`.
10. **Hostile names** (`</script>`, `operator<<`, quotes, `onmouseover=`, `<img onerror>`, non-ASCII,
    `http://`): exactly two script tags, no injected tags or `on*` attributes, data decodes back to the
    exact names.
11. **Size budget** and **`write_html`** (refuses `.aerotrace/`, says when it replaced a file).

Browser tests (`@pytest.mark.browser`, skipped by default like `realrepo`; run with
`uv run --with playwright pytest -m browser tests/ui`): hostile page runs nothing
(`window.__pwned` stays unset) and requests nothing but itself, no console errors or CSP violations,
keyboard (Home, arrows, F, Escape, `/`, Enter) works, render time budgets (1,000 nodes ≤ 300 ms, 5,000
≤ 1 s). Uses Playwright's Chromium if installed, else a local Chrome.

Manual checks done with the Playwright MCP: light and dark screenshots, theme toggle, focus, search,
details pane, network log empty.

## 6. README draft

`docs/launch/README.draft.md`: one-line pitch, demo video placeholder, one install command (the same
`uv tool install` line as README.md and `install.py`), `{{BENCH_HEADLINE}}` on its own line for item 8,
how it works in 3 steps, the three assistants exactly as `mcp/install.py` writes them, honest limits,
Apache-2.0.

## 7. Not in this PR

- `aerotrace check` itself and its CLI flags (Part C). Part C calls `ui.render_text`, `ui.style_for`,
  `ui.write_html` and `ui.check`, and should add a contract test of its own output.
- Screenshots on a real Redis change (needs Part C).

## 8. Risks

- **R1. Grey "no effect" could read as safe.** Labelled "NO EFFECT FOUND", used only for nodes Part C
  explicitly checked; Part C may send none.
- **R2. Contract drift with Part C.** Versioned JSON, a shared fixture file, strict `check()`.
- **R3. Emoji width** varies by terminal; nothing is aligned after the headline emoji, and item lines use
  one-column symbols.
- **R4. Yellow on light terminals** can be faint; it is only on a bold word that is also spelled out.
- **R5. Very large changes**: Part C caps at 5,000 nodes and reports `omitted`; the header and status
  line show it.
- **R6. Browsers**: tested in Chrome/Chromium. Firefox and Safari are a manual check (file:// and
  `localStorage` differ a little). ES5-style JS plus a few ES2015 APIs (typed arrays, `classList`).
- **R7. Golden churn**: any CSS/JS edit changes the HTML goldens. Intended; one command regenerates them.

## 9. Review (independent reviewer, "go with changes") and what was done

| # | point | done |
|---|-------|------|
| 1 | MUST: "breaks" = will not build or link only; only Part C sets it; can be probable; headline counts only exact; reject breaks+unknown | Sections 1.1, 1.2, 2.1. `summary.breaks_exact` / `breaks_probable`; "MAY BREAK" word; `test_only_exact_breaks_count_in_the_headline`, `test_break_can_not_be_unknown`. |
| 2 | MUST: separate "is the link real?" from "is the effect real?" | Section 1.1. `certainty` = link, `status` = effect, `link` = raw label. Unknown status and certainty must go together. |
| 3 | MUST: never a wrong ✅; DiffImpact gaps in `change`; "new" status; blind-spot scope | `change.not_followed` (outside, not_indexed, deleted, mismatched, unreadable, new_files), `new_functions` and status `new`, `summary.not_followed`, `scope: change/index`, ℹ️ headline. `check` refuses gaps without a count. Truncation is `proof.stopped_early`. |
| 4 | MUST: proof line must not overclaim | Now `index: N files, N functions; followed callers up to N steps (stopped early: yes/no)`. The exact/probable/unknown counts were dropped from it. |
| 5 | MUST: ids; via is a name; removed key; cap drops edges/via | Section 1.2. `via` id + `via_name`; `removed:{file}:{line}:{name}`; `check` refuses dangling ids. |
| 6 | SHOULD: CSP with hashes, base-uri, form-action, no img-src; charset first; href only `#`; only SVG namespace; no xlink; no `//` comments | Section 3.7; `test_csp_hashes_and_order`, `test_no_external_urls`, `test_assets_have_no_line_comments_or_urls`. |
| 7 | SHOULD: escape every Python-rendered field and attribute; test it | `html.escape(quote=True)` everywhere; `test_hostile_names_stay_text` and the browser hostile test. |
| 8 | SHOULD: screen readers | Section 3.5: list is the main path; graph is one `role="application"` stop with `aria-activedescendant` and a live line; list says what is hidden in focus/search. |
| 9 | SHOULD: bubbles say what is inside; deep BREAKS/UNKNOWN show their chain | Section 3.4. |
| 10 | SHOULD: simpler drawing; index edges; size budget | Section 3.3; size budget tested (≤ 2.5 MB at 5,000; measured 2.0 MB). |
| 11 | SHOULD: HTML for no_index, index_error, nothing_to_check, 0 nodes | Goldens for all four; `test_empty_states_have_no_graph_and_no_all_clear`. |
| 12 | SHOULD: `@pytest.mark.browser` Playwright test; Part C contract test later | `tests/ui/test_ui_browser.py`, marker registered, skipped by default. Part C note in sections 1.2 and 7. |
| 13 | NIT: clipboard fallback; allowlist `navigator.clipboard` | Done (selects the text when the clipboard API fails). |
| 14 | NIT: R1 resolved | Owner asked for `--html`; noted in section 0. |
| 15 | NIT: direct counts first | "(2 direct, 4 further up)". |
| 16 | NIT: refuse `--html` inside `.aerotrace/`; say when overwriting | `write_html`; tested. |

## 10. Changes after PR review (#38)

| # | point | done |
|---|-------|------|
| 1 | MUST: `check()` must not stop early for non-ok statuses; `nothing_to_check` with skipped parts lists them, never ✅ | `check()` validates `change` for every status, requires it for `nothing_to_check`, and refuses nodes/edges on non-ok data. The headline becomes "ℹ️ No change AeroTrace could check (...), but N parts of it could not be followed." with the list, in terminal and HTML. Golden `nothing_to_check_gaps`. |
| 2 | SHOULD: reject contradictions | With nothing omitted, summary counts must equal the nodes (direct/further split included). An all-zero summary with omitted nodes or `stopped_early` is refused. |
| 3 | SHOULD: no node outside a section; no duplicates | Every non-change node is in exactly one of functions/data; duplicates and changed nodes in sections are refused. |
| 4 | SHOULD: terminal names what changed | New "What changed (N)" section, one line per changed/new/removed node. |
| 5 | SHOULD: non-UTF-8 streams | ASCII mode escapes non-ASCII with `backslashreplace`; tested with the hostile names. |
| 6 | SHOULD: README overclaims | Pitch says "says how sure it is about every link"; limits say many links are `probable`; the `void *` claim is gone; test mapping is "coming"; the install URL has a note that it works once the repo is public. |
| 7 | SHOULD: `write_html` must not overwrite non-HTML files | Refuses any name not ending in .html/.htm (so never source code); says "Replaced the existing HTML file". |
| 8 | Decision: ✅ only when nothing in the change was skipped; index limits under a ✅ | Done: `Index limit: ...` lines right under the ✅ headline (up to 5, then "and N more, listed below"). |
| 9 | NIT: bidi and line separators | Shown as `\uXXXX` in terminal and HTML. |
| 10 | NIT: NO_COLOR controls colour only | `style_for` keeps Unicode icons unless the stream isn't UTF-8 or TERM=dumb. ASCII goldens renamed `*.ascii.txt`. |
| - | Contract: `new:{file}:{line}:{name}` ids | Added to section 1.2 and `schema.py`. |
