# Plan: launch UI (work item 9)

Status: plan, for review. No code yet.
Branch: `feat/launch-ui` (from `origin/launch-candidate`).

What this item builds:

1. A terminal renderer for `aerotrace check` (the command itself comes in item 5 Part C).
2. `aerotrace check --html <out.html>`: one self-contained, offline HTML report.
3. `docs/launch/README.draft.md` (README.md itself is not touched).
4. Later, not now: screenshots of a real Redis change in `docs/launch/screenshots/`.

Tooling note: no "Open UI" MCP tools are available in this session. The HTML, CSS and JS are written by
hand, with no framework, no build step and no new runtime dependency.

## 0. Rules this plan must keep

- **No analysis logic in the renderer.** It never decides what breaks, what is affected, or how sure a
  link is. Part C puts every status, certainty, reason and count into the JSON. The renderer formats,
  caps lists for the terminal, and lays out the graph. That is all.
- **Deterministic.** Same JSON in, byte-identical text and HTML out. No timestamps, no machine paths, no
  random numbers, no hash-order iteration.
- **Fully local.** The HTML makes no network request of any kind. A Content-Security-Policy in the file
  enforces this in the browser too (section 3.7).
- **Never guess silently / a wrong "safe" is the worst bug.** Blind spots are always shown, also when
  nothing is affected. Grey nodes are labelled "no effect found", never "safe" (see risk R2).
- **Product rule 7** in CLAUDE.md says "dashboards or web UI" are out of scope. A static report file the
  user asks for with a flag is not a server or a dashboard, but the owner should confirm (risk R1).

## 1. Input: the `aerotrace-check` JSON, version 1

Part C produces this; both renderers read it. `aerotrace check --json` should print exactly this object,
so agents and the HTML see the same facts. A sample file lives at
`tests/fixtures/ui/check-v1-sample.json` and is the contract between this item and Part C.

```jsonc
{
  "format": "aerotrace-check",       // fixed string
  "version": 1,                       // bump on any breaking change; renderer refuses a newer major
  "tool": "aerotrace 0.1.0",          // tool version, no build date
  "status": "ok",                     // "ok" | "no_index" | "index_error" | "nothing_to_check"
  "message": null,                    // plain-English text when status != "ok"

  "change": {
    "mode": "git",                    // "git" | "patch"
    "base": "HEAD",                   // what we compared with (git mode), else null
    "files": 2,                       // C/C++ files the change touches
    "other_files": 1,                 // non-C/C++ files in the diff (ignored)
    "changed_functions": 2,
    "removed_functions": 1,
    "changed_declarations": 1
  },

  "summary": {                        // the headline numbers; renderer prints them, never recomputes
    "breaks": 3,                      // things outside the change that will not build/link/behave
    "affected": 9,                    // probable impact
    "unknown": 2,                     // impact could not be followed
    "no_effect": 0                    // context nodes checked and found unaffected (grey)
  },

  "nodes": [                          // every function/declaration in the picture, already sorted
    {
      "id": "c:@F@setrangeCommand",   // stable index id (USR-based), unique in this file
      "name": "setrangeCommand",      // qualified name, for display
      "kind": "function",             // "function" | "field" | "global" | "static_local"
      "file": "src/t_string.c",       // repo-relative, forward slashes
      "line": 612,
      "status": "breaks",             // "changed" | "removed" | "breaks" | "affected" | "unknown" | "no_effect"
      "certainty": "exact",           // "exact" | "probable" | "unknown"  (analysis/certainty.py levels)
      "link": "calls_removed",        // raw label from graph.CONFIDENCE_ORDER, or null for changed nodes
      "why": "still calls checkStringLength, which this change removes",
      "distance": 1,                  // call/data steps from the nearest changed node; 0 for changed
      "via": "c:@F@checkStringLength",// id one step closer to the change, or null
      "lines": [619]                  // lines of the link inside this node's file (may be empty)
    }
  ],

  "edges": [                          // sorted by (from, to, kind)
    { "from": "c:@F@setrangeCommand", "to": "c:@F@checkStringLength",
      "kind": "calls", "certainty": "exact" }
                                      // kind: "calls" | "reads" | "writes"; from -> to in code direction
  ],

  "sections": {                       // what the terminal lists, in this order; values are node ids
    "functions": ["c:@F@setrangeCommand", "..."],
    "data": ["c:@S@client@FI@bufpos", "..."],
    "tests_to_run": [
      { "name": "tests/unit/type/string.tcl", "covers": ["c:@F@setrangeCommand"], "why": "calls setrangeCommand" }
    ],
    "untested_changes": [ { "node": "c:@F@getGenericCommand", "why": "no test reaches it" } ],
    "blind_spots": [ { "text": "3 calls through function pointers in src/t_string.c were matched by type only.",
                       "node": null } ]
  },

  "omitted": { "nodes": 0 },          // Part C caps the graph at 5,000 nodes and says how many it left out

  "proof": { "files": 117, "functions": 5064, "exact": 3, "probable": 9, "unknown": 2 }
}
```

Contract details:

- **Order is the producer's job.** Part C sorts `nodes` by (status rank, distance, file, line, id) and
  each section the same way. The renderer keeps the given order. It sorts nothing by itself except
  inside the graph layout (section 3.3), where the order is fixed by the layout rule.
- **Sections refer to node ids**, so a fact is written once. Every id in `sections`, `edges` and `via`
  must exist in `nodes`; the renderer checks this and stops with a clear error if not.
- **Counts are explicit.** Lists may be capped by Part C, so the renderer never counts list lengths to
  build a headline. `summary` and `proof` are the only source of the numbers.
- **Tests sections are optional.** If Part C has no test mapping yet, it sends `"tests_to_run": null`
  and `"untested_changes": null`, and the terminal prints "Tests: not known yet (no test map in the
  index)." It never prints an empty "Tests to run (0)", which would look like "no tests needed".
- Validation lives in `src/aerotrace/ui/schema.py` (TypedDicts plus a `check(data)` that only checks
  shape, version and ids; no analysis).

## 2. Terminal output

Code: `src/aerotrace/ui/terminal.py`, one pure function
`render(data, *, color: bool, unicode: bool, limit: int = 8) -> str`, and
`style_for(stream, env) -> (color, unicode)` that picks the mode:

| situation                                  | colour | icons   |
|--------------------------------------------|--------|---------|
| TTY, UTF-8, no NO_COLOR, TERM != dumb       | yes    | Unicode |
| not a TTY (pipe, file, CI log), UTF-8      | no     | Unicode |
| `NO_COLOR` set to any non-empty value      | no     | ASCII   |
| stream encoding is not UTF-8               | no     | ASCII   |
| TERM=dumb                                  | no     | ASCII   |

Icon table (every icon always sits next to a text word, so meaning never depends on colour or glyph):

| meaning         | Unicode | ASCII  | word        | colour (TTY only)    |
|-----------------|---------|--------|-------------|----------------------|
| headline, risk  | ⚠️      | `[!]`  | -           | bold                 |
| headline, clear | ✅      | `[ok]` | -           | bold                 |
| breaks          | ✖       | `[x]`  | `BREAKS`    | bold red (SGR 1;31)  |
| affected        | ▲       | `[~]`  | `AFFECTED`  | bold yellow (1;33)   |
| unknown         | ?       | `[?]`  | `UNKNOWN`   | bold (no hue)        |
| no effect       | ·       | `[-]`  | `NO EFFECT` | none                 |
| no test         | ○       | `[ ]`  | `NO TEST`   | none                 |

Light and dark terminals: only SGR 1 (bold), 31 and 33 are used, only on the icon and the status word.
No dim (SGR 2), no grey, no background colours, no white or black foreground. Reasons, names and paths
stay in the terminal's default colour. The renderer does not wrap lines (keeps output grep-able and
deterministic); each item is two lines so narrow terminals still read well. Numbers use `1,234` style,
fixed (not locale-dependent).

The mock-ups below are word for word what the renderer prints for `tests/fixtures/ui/*.json`. The names
are Redis-flavoured but illustrative; they are not tool output and not golden facts.

### 2.1 Normal (UTF-8, colour on a TTY; same text without escapes when piped)

```text
⚠️  This change can break 3 things you didn't touch.
    9 more may be affected and 2 could not be followed.
    You changed 2 functions, removed 1 function and changed 1 declaration in 2 files (compared with HEAD).

Functions (10)
  ✖ BREAKS     setrangeCommand  src/t_string.c:612
               still calls checkStringLength, which this change removes. Sure: exact.
  ✖ BREAKS     appendCommand  src/t_string.c:688
               still calls checkStringLength, which this change removes. Sure: exact.
  ✖ BREAKS     getCommand  src/t_string.c:398
               passes 1 argument; getGenericCommand now takes 2. Sure: exact.
  ▲ AFFECTED   getexCommand  src/t_string.c:421
               calls getGenericCommand, 2 steps away via getCommand. Sure: probable (same name in 2 files).
  ▲ AFFECTED   getdelCommand  src/t_string.c:470
               calls getGenericCommand through a function pointer of the same type. Sure: probable.
  ▲ AFFECTED   lcsCommand  src/t_string.c:890
               calls getGenericCommand through a function pointer of the same type. Sure: probable.
  ▲ AFFECTED   call  src/server.c:3780
               runs every command through c->cmd->proc, which may be getCommand. Sure: probable.
  ▲ AFFECTED   processCommand  src/server.c:4221
               3 steps away via call. Sure: probable.
  ... and 2 more (1 AFFECTED, 1 UNKNOWN). See all: aerotrace check --limit 20, or --html report.html

Data (4)
  ▲ AFFECTED   client.bufpos (field)  src/server.h:1201
               its type changed; _addReplyToBuffer reads it. Sure: probable.
  ▲ AFFECTED   client.buf (field)  src/server.h:1200
               written next to bufpos in _addReplyToBuffer. Sure: probable.
  ▲ AFFECTED   server.stat_net_output_bytes (global)  src/server.h:1890
               written by _addReplyToBuffer, read by 4 functions. Sure: probable.
  ? UNKNOWN    client.reply (field)  src/server.h:1205
               passed to listAddNodeTail as void *; what happens next is not known. Sure: unknown.

Tests to run (2)
  tests/unit/type/string.tcl      covers setrangeCommand, appendCommand, getCommand
  tests/unit/type/incr.tcl        covers getCommand

Untested changes (1)
  ○ NO TEST    getGenericCommand  src/t_string.c:380
               no test reaches it.

Blind spots (2)
  ? 3 calls through function pointers in src/t_string.c were matched by type only.
  ? src/t_string.c: lines 40-44 are outside any function (a macro); AeroTrace cannot follow macro uses.

checked 117 files, 5,064 functions; 3 exact, 9 probable, 2 unknown
```

### 2.2 NO_COLOR=1 (also: non-UTF-8 stream, TERM=dumb)

Same words, ASCII icons, no escape codes. Only the first column changes.

```text
[!] This change can break 3 things you didn't touch.
    9 more may be affected and 2 could not be followed.
    You changed 2 functions, removed 1 function and changed 1 declaration in 2 files (compared with HEAD).

Functions (10)
  [x] BREAKS     setrangeCommand  src/t_string.c:612
                 still calls checkStringLength, which this change removes. Sure: exact.
  [x] BREAKS     appendCommand  src/t_string.c:688
                 still calls checkStringLength, which this change removes. Sure: exact.
  [x] BREAKS     getCommand  src/t_string.c:398
                 passes 1 argument; getGenericCommand now takes 2. Sure: exact.
  [~] AFFECTED   getexCommand  src/t_string.c:421
                 calls getGenericCommand, 2 steps away via getCommand. Sure: probable (same name in 2 files).
  [~] AFFECTED   getdelCommand  src/t_string.c:470
                 calls getGenericCommand through a function pointer of the same type. Sure: probable.
  [~] AFFECTED   lcsCommand  src/t_string.c:890
                 calls getGenericCommand through a function pointer of the same type. Sure: probable.
  [~] AFFECTED   call  src/server.c:3780
                 runs every command through c->cmd->proc, which may be getCommand. Sure: probable.
  [~] AFFECTED   processCommand  src/server.c:4221
                 3 steps away via call. Sure: probable.
  ... and 2 more (1 AFFECTED, 1 UNKNOWN). See all: aerotrace check --limit 20, or --html report.html

Data (4)
  [~] AFFECTED   client.bufpos (field)  src/server.h:1201
                 its type changed; _addReplyToBuffer reads it. Sure: probable.
  [~] AFFECTED   client.buf (field)  src/server.h:1200
                 written next to bufpos in _addReplyToBuffer. Sure: probable.
  [~] AFFECTED   server.stat_net_output_bytes (global)  src/server.h:1890
                 written by _addReplyToBuffer, read by 4 functions. Sure: probable.
  [?] UNKNOWN    client.reply (field)  src/server.h:1205
                 passed to listAddNodeTail as void *; what happens next is not known. Sure: unknown.

Tests to run (2)
  tests/unit/type/string.tcl      covers setrangeCommand, appendCommand, getCommand
  tests/unit/type/incr.tcl        covers getCommand

Untested changes (1)
  [ ] NO TEST    getGenericCommand  src/t_string.c:380
                 no test reaches it.

Blind spots (2)
  [?] 3 calls through function pointers in src/t_string.c were matched by type only.
  [?] src/t_string.c: lines 40-44 are outside any function (a macro); AeroTrace cannot follow macro uses.

checked 117 files, 5,064 functions; 3 exact, 9 probable, 2 unknown
```

### 2.3 Nothing affected

The headline never says "safe". Blind spots are always printed. If `summary.unknown > 0`, the
"nothing found" headline is not used; the risk headline variant "...could not be followed" is used.

```text
✅ Nothing found that this change can break, in the code AeroTrace can see.
    You changed 1 function in 1 file (compared with HEAD).
    No other indexed function calls it or reads what it writes.

Tests to run (1)
  tests/unit/type/string.tcl      covers getrangeCommand

Blind spots (1)
  ? 3 calls through function pointers in src/t_string.c were matched by type only.

checked 117 files, 5,064 functions; 0 exact, 0 probable, 0 unknown
```

(NO_COLOR: `[ok]` instead of `✅`, `[?]` instead of `?`; otherwise identical.)

Headline variants, chosen only from `summary` numbers:

| summary                          | headline                                                                 |
|----------------------------------|--------------------------------------------------------------------------|
| breaks > 0                       | `⚠️  This change can break N thing(s) you didn't touch.`                 |
| breaks = 0, affected > 0         | `⚠️  This change may affect N thing(s) you didn't touch. None is sure to break.` |
| breaks = affected = 0, unknown > 0 | `⚠️  N thing(s) this change reaches could not be followed. Check them by hand.` |
| all 0                            | `✅ Nothing found that this change can break, in the code AeroTrace can see.` |

The second line lists the remaining non-zero counts ("9 more may be affected and 2 could not be
followed."), and is left out when they are all 0.

### 2.4 Index missing (`status: "no_index"`)

```text
[!] AeroTrace has no index for this repo yet, so it checked nothing.
    Run `aerotrace index` first (Redis takes about a minute), then run `aerotrace check` again.
```

Shown in ASCII mode; Unicode mode prints `⚠️ ` instead of `[!]`. It never uses the "clear" icon:
nothing was checked, so it must never look like a pass. `index_error` prints Part C's `message` in the same two-line form.
`nothing_to_check` (empty diff) prints `✅ No C or C++ changes to check (compared with HEAD).` and the
"(N other files in the diff are not C or C++.)" line if `other_files > 0`.
Exit codes are Part C's call; the suggestion is 0 = nothing breaks, 1 = something breaks, 2 = could not
check (no index, error). `no_index` must never exit 0.

## 3. HTML report

Code: `src/aerotrace/ui/html.py` (`render(data) -> str`) plus three hand-written assets in
`src/aerotrace/ui/assets/`: `report.html` (skeleton with placeholders), `report.css`, `report.js`.
`html.py` reads them with `importlib.resources` and inlines them. Hatch already ships non-Python files
inside `src/aerotrace`, so no packaging change.

### 3.1 Layout

```text
+--------------------------------------------------------------------------------------+
| ⚠ This change can break 3 things you didn't touch.   [search......]  [Theme: auto v] |
| 9 more may be affected, 2 could not be followed.  checked 117 files, 5,064 functions |
+---------------------------+------------------------------------------+---------------+
| LIST (keyboard friendly)  |                GRAPH (SVG)               | DETAILS       |
| > Functions (10)          |         o   o                            | setrange...   |
|   x BREAKS setrange...    |      o    [◆ changed]   o                | src/t_str:612 |
|   x BREAKS append...      |         ▲    |    ✖                      | BREAKS        |
| > Data (4)                |       ▲   ▲  +12 more                    | Why: still... |
| > Tests to run (2)        |                                          | Sure: exact   |
| > Untested changes (1)    |  [+] [-] [fit] [focus] [expand all]      | Path: a > b   |
| > Blind spots (2)         |  Legend: ✖ breaks ▲ affected ? unknown   | Lines: 619    |
+---------------------------+------------------------------------------+---------------+
```

- Header: the same headline and proof line as the terminal, and the blind-spot count as a link to its
  list. The headline is plain HTML text, so it is readable even if JS fails.
- Left: the sections as `<details>` lists, the same order and words as the terminal, but uncapped.
- Centre: the graph. Right: details of the selected node.
- Narrow window (< 900px): the three panes stack; the list comes first.
- A `<noscript>` block and the server-rendered list mean the report still shows every fact without JS.

### 3.2 Status: colour + shape + icon + word

| status    | fill (light / dark)    | shape           | icon in node | text label   |
|-----------|------------------------|-----------------|--------------|--------------|
| changed   | blue outline, thick    | diamond         | ◆            | CHANGED      |
| removed   | blue outline, dashed   | diamond, struck | ✕            | REMOVED      |
| breaks    | red #b42318 / #f97066  | square          | ✖ (SVG path) | BREAKS       |
| affected  | amber #b54708 / #fdb022| triangle        | ! (SVG path) | AFFECTED     |
| unknown   | white / dark, dashed   | circle, dashed  | ?            | UNKNOWN      |
| no effect | grey #667085 / #98a2b3 | small circle    | -            | NO EFFECT FOUND |

- Shapes and icons are SVG paths, not font glyphs (no font dependency; system font stack only).
- Every node has `role="button"`, an `aria-label` like "BREAKS: setrangeCommand, src/t_string.c line 612,
  exact", and a `<title>` for hover.
- The legend is always visible and uses the same shapes and words.
- Colours are checked for 3:1 contrast against both backgrounds (non-text) and text labels for 4.5:1.
  They are also checked to stay distinct under deuteranopia/protanopia simulation, but they don't have to:
  shape and word carry the meaning.

### 3.3 How the graph is drawn (no libraries)

- Plain SVG built with `document.createElementNS` and `textContent` (never `innerHTML` with data, so
  names like `operator<` or `</script>` are safe).
- **Radial layered layout**, computed once in JS for all nodes, fully deterministic:
  - ring 0 (centre): changed and removed nodes, in given order (one node: at the centre; several: a small
    inner circle).
  - ring k: nodes at `distance` k. Within a ring, nodes are ordered by the angle of their `via` parent,
    then by status rank, then by the given order. This keeps children near their parent and cuts edge
    crossings, without any force simulation.
  - ring radius `r_k = max(k * 140, n_k * 28 / (2 * pi))`, so a big ring grows instead of overlapping.
- Edges: straight lines. All edges of one kind/status go into **one `<path>`** (many `M x y L x y`
  segments), so 3,000 edges cost a handful of DOM elements. The selected node's edges are drawn again in
  a highlight path on top.
- Pan with drag, zoom with wheel or `+`/`-` buttons, "fit" button. Done by changing the SVG `viewBox`
  inside `requestAnimationFrame`.
- Level of detail: labels show for changed, breaks, selected, searched and focused nodes always, and for
  other nodes only when zoomed in enough that labels don't overlap (a simple zoom threshold).

### 3.4 Collapsed by default, search, focus

- **Collapsed by default:** ring 0 and ring 1 are shown, capped at 40 nodes per ring. Deeper rings and
  the rest of a big ring are drawn as one "+N more (distance k)" bubble; click or Enter expands that
  ring. **BREAKS and UNKNOWN nodes are never hidden by collapsing**; they are always drawn, at any
  distance. "Expand all" shows everything.
- **Search box** (also `/`): matches name, file, and `file:line`, case-insensitive, over strings
  precomputed at load. Matches are highlighted in the graph (others faded, never hidden, still labelled
  by shape), the left list filters to matches, and an `aria-live` line says "4 matches". Enter selects
  the first match and pans to it.
- **Focus mode** (button or `f` on a selected node): shows only that node, its `via` chain back to the
  change, and its direct neighbours (edges in and out). Capped at 200 neighbours with a "+N more" note.
  Esc or "Show all" leaves focus mode. The header says "Focus: setrangeCommand (12 of 1,024 nodes
  shown)" so the user always knows the view is partial.
- Selecting a node (click, Enter, or in the list) fills the details pane: name, kind, `file:line`,
  status word, why, how sure (exact/probable/unknown plus the meaning text from `certainty.MEANING`,
  passed in the JSON, not hard-coded in JS), raw link label, path back to the change (via chain, each
  step clickable), link lines, and a "copy file:line" button.

### 3.5 Keyboard and accessibility

- Tab order: search, theme toggle, list, graph, details. The graph is **one** tab stop (roving
  tabindex), so 1,000 nodes don't mean 1,000 tab presses.
- In the graph: arrow keys move to the nearest node in that direction, `Home` jumps to the changed
  node, Enter selects, `f` focus, `Esc` clear focus/search, `+`/`-`/`0` zoom/fit, `e` expand ring.
- Visible focus ring on everything (2px outline, both themes).
- The left list is a full, screen-reader-friendly alternative to the graph: every fact in the graph is
  also in the list.
- `prefers-reduced-motion`: no animated pan/zoom.

### 3.6 Light and dark

- CSS variables for every colour. `@media (prefers-color-scheme: dark)` sets the dark values.
- A toggle (auto / light / dark) sets `data-theme` on `<html>`, and remembers it in `localStorage`
  inside a `try` (file:// may block it; then it just doesn't remember). Nothing is written into the
  file itself, so determinism is kept.

### 3.7 Self-contained, offline, deterministic

- One file: CSS in `<style>`, JS in `<script>`, data in `<script type="application/json" id="data">`.
- Data is `json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)` with `<`, `>`,
  `&` also escaped as `\u003c`, `\u003e`, `\u0026`, so no data can close the script tag.
- `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline';
  script-src 'unsafe-inline'; img-src data:">`: even a bug can't load anything from the network.
- No external URLs at all. The only `http://` string allowed is the SVG namespace
  `http://www.w3.org/2000/svg` used by `createElementNS` (it is an identifier, not a request).
- No timestamp, no absolute path, no hostname, no random ids. Written with `\n` line endings and UTF-8.
- Size: about 60 KB of code + the data (about 250 KB for 1,000 nodes).

## 4. Performance (1,000+ nodes)

Approach:

- Lay out all nodes once (O(n log n)), draw only the visible ones.
- Edges batched into a few `<path>` elements; event handling by delegation on the SVG (one listener).
- Search over precomputed lowercase strings, debounced 80 ms; list results capped at 200 with a "+N
  more" line.
- Labels by level of detail; no CSS filters or shadows on nodes.
- No layout reads inside loops (no forced reflow); one DOM append per render via `DocumentFragment`.

Budgets (headless Chromium on a laptop), measured from script start to first frame:

| data                         | first render | expand all | search keystroke |
|------------------------------|--------------|------------|------------------|
| 1,000 nodes / 3,000 edges    | < 300 ms     | < 1 s      | < 50 ms          |
| 5,000 nodes / 15,000 edges   | < 1 s        | < 3 s      | < 100 ms         |

Measuring: `report.js` writes the time into `document.documentElement.dataset.renderMs` after the
first frame (runtime only, never in the file). `tests/ui/make_big_check.py` makes deterministic large
inputs (fixed seed `random.Random(9)`, in tests only). The check opens the file in headless Chromium
(Playwright MCP from the coordinator, or the `webapp-testing` Playwright scripts if installed locally)
and reads `renderMs`, then clicks "expand all" and types in search. Results go in the PR description.
This is a manual/dev check, not a pytest test, because CI has no browser and we don't add one.

## 5. Test plan

All new tests are plain pytest, no browser, no network.

1. **Terminal goldens** (`tests/ui/test_terminal.py`, goldens in `tests/golden/ui/`):
   - `check-v1-sample.json` -> `check.txt` (Unicode, no colour), `check.nocolor.txt` (ASCII),
     `check.ansi.txt` (colour; escapes visible in the golden).
   - `check-v1-nothing.json` -> `nothing.txt`, `nothing.nocolor.txt`.
   - `check-v1-noindex.json` -> `noindex.txt`.
   - variants: breaks=0/affected>0, unknown-only, tests `null`, `--limit` cap text.
   - The mock-ups in section 2 are these goldens.
2. **Mode picking**: `style_for` with fake streams for each row of the table in section 2 (TTY,
   pipe, NO_COLOR=1, NO_COLOR="" which per no-color.org does not count, latin-1 encoding, TERM=dumb).
3. **No colour-only meaning**: strip ANSI from the colour output and assert it equals the no-colour
   output; assert every item line has a status word.
4. **HTML golden snapshot**: `check-v1-sample.json` -> `tests/golden/ui/check.html`, compared byte for
   byte. An `UPDATE_GOLDEN=1` switch rewrites it, so reviewers see the diff.
5. **Determinism**: render twice in-process; render in two subprocesses with different
   `PYTHONHASHSEED`; render the same data with dict keys in a different insertion order. All must give
   identical bytes.
6. **No external URL**: scan the HTML for `http://`, `https://`, `//` in `src`/`href`, `url(`,
   `@import`, `<link`, `<img`, `<iframe`, `fetch(`, `XMLHttpRequest`, `WebSocket`, `EventSource`,
   `import(`, `sendBeacon`, `navigator.` network APIs. Allowlist: exactly the SVG namespace string.
   Also assert the CSP meta tag is present and is the first thing in `<head>`.
7. **Escaping**: names with `</script>`, `<!--`, `&amp;`, quotes, `operator<<`, and non-ASCII must
   come out escaped in the data block, and the HTML must still parse (stdlib `html.parser`, no errors,
   one `<script type="application/json">`).
8. **Contract checks**: unknown `format`, `version` 2, missing ids, ids in sections not in nodes ->
   a clear error, no half output.
9. **Accessibility (static)**: the legend contains all six status words; the status table (colour,
   shape, icon, word) is defined once in Python and passed to the JS inside the data block, and a test
   asserts every status has all four. The JS draws a node only from that table, so there is no
   colour-only path.
10. **Manual, recorded in the PR**: Playwright run of the 1,000 and 5,000 node files (timings),
    keyboard-only walk-through, light/dark screenshots via `emulate_media`, network log shows zero
    requests.

Gate before each commit: `uv run ruff check . && uv run ruff format --check . && uv run pytest`;
before the PR also `uv run pytest -m realrepo -rxX` (must stay at the same 3 known failures).

## 6. README draft (`docs/launch/README.draft.md`)

Sections: one-line pitch; demo video placeholder (`<!-- DEMO VIDEO: docs/launch/demo.mp4 -->`); one
install command (`uv tool install git+https://github.com/aayushswami123/aerotrace`, same as today's
README and the `install.py` hint); `{{BENCH_HEADLINE}}` exactly as written, on its own line, for item 8;
how it works in 3 steps (index with libclang -> diff mapped to functions and data -> ranked answer with
certainty and blind spots); supported assistants exactly as `mcp/install.py` implements them
(`--client claude` -> `.mcp.json`, `cursor` -> `.cursor/mcp.json`, `codex` -> `~/.codex/config.toml` or
`$CODEX_HOME`); honest limits (C/C++ only, needs `compile_commands.json`, macros and types not
followed, function pointers matched by type, Linux/macOS tested); license Apache-2.0.

## 7. Work order

1. This plan (now). Review.
2. `ui/schema.py` + fixture JSONs + terminal renderer + goldens.
3. HTML renderer + assets + golden/determinism/no-URL/escaping tests.
4. Big-data generator + Playwright timing check; tune.
5. README draft.
6. PR to `launch-candidate`. Screenshots later, once Part C exists and runs on Redis.

## 8. Risks

- **R1. Product rule 7 ("no dashboards or web UI").** A static, offline file is not a server, but the
  owner should say yes. If not, the HTML part is dropped and the terminal part stands alone.
- **R2. Grey "no effect" could read as "safe".** A wrong safe is the worst bug. Grey is only for nodes
  Part C explicitly checked (for example callees of a changed function, for orientation). The label
  says "NO EFFECT FOUND", the details pane repeats the blind spots, and Part C may send none at all.
- **R3. Contract drift with Part C.** Mitigated by the versioned JSON, the shared fixture file, and the
  renderer refusing unknown versions. Part C should add its own test that its output passes
  `ui.schema.check`.
- **R4. "Breaks" vs "affected" is Part C's call.** The renderer can't fix a wrong status. The mock-ups
  assume `calls_removed` and "does not compile now" sites are BREAKS; Part C should confirm.
- **R5. Emoji width.** `⚠️` is two columns in most terminals and one in some. Nothing is aligned after
  it on the headline line, and item lines don't use emoji, so misalignment can't hurt.
- **R6. Yellow on light terminals** can be faint. It is only on the icon and word, which are bold and
  also spelled out, so the text still reads.
- **R7. Very large changes** (10,000+ nodes) make a big file and a slow first paint. Part C caps at
  5,000 nodes and reports `omitted`; the HTML header shows it.
- **R8. Browsers.** Tested in Chromium. Firefox and Safari are a manual check (CSP meta on file:// and
  `localStorage` behave a bit differently). Only ES2018 features, no modules.
- **R9. Golden HTML churn.** Any CSS/JS edit changes the snapshot. That is intended (reviewers see it),
  but the golden is regenerated with one command to keep it cheap.
