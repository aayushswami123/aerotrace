# Plan: benchmark, results page and local dashboard

Status: Phase 0, owner decisions recorded 2026-11-24 (section 8).
Branch: `docs/dashboard-plan` (from `origin/launch-candidate` e747172).

Three pieces, one design:

1. **Benchmark** (the proof): a fair, repeatable accuracy test on pinned Redis, SQLite and curl.
2. **Results page** (the story): `docs/benchmark/index.html`, one offline file built from the results JSON.
3. **Dashboard** (the product): `aerotrace ui`, a local, read-only web app over the index and saved checks.

All three use the same tokens file (`src/aerotrace/ui/web/tokens.css`) and the same status words, icons
and colours. See `docs/design/README.md`.

## 0. Rules this keeps

- **Honest numbers only.** The results page and the dashboard's Benchmark page are generated from
  `bench/results/<run>/results.json`. No number is ever typed into HTML. Mockups use sample numbers and
  say so in a banner on every screen.
- **Local only.** The server listens on `127.0.0.1` only. Pages load nothing from the internet: no CDN,
  no remote fonts, no remote images. A Content-Security-Policy (`default-src 'self'`) stops that even if
  there were a bug.
- **Read-only.** The server only answers `GET`. It never writes into the user's source tree, never runs
  their build, and never runs `aerotrace check` for them. It reads `.aerotrace/` and nothing else.
- **No analysis in the UI.** The dashboard shows what `analysis/` put in the JSON. It does not decide
  what breaks. (Same rule as `docs/plans/launch-ui.md` section 0.)
- **Deterministic.** Same index and same saved checks give the same API answers (sorted, no machine
  paths). The run date shown on the Changes page is stored *next to* each check, not inside it (see 3.3).

**Product rule 7 (decided).** CLAUDE.md now says "only a local, offline, read-only UI", and ADR
`docs/decisions/0008-local-dashboard.md` records why. Both are in this PR.

**Commits (decided).** I commit on my own feature branches, never on `main` or `launch-candidate`. Before
each PR I rebase on the latest `launch-candidate`, then open the PR and run a reviewer agent on it. The
owner merges.

## 1. Architecture

```
            .aerotrace/index.db          .aerotrace/checks/*.json      bench/results/<run>/results.json
                    |                            |                                   |
                    v                            v                                   |
   analysis/  (existing: status, graph, data, check)  <-- the only place facts come from    |
                    |                                                                |
                    v                                                                v
   ui/api.py   (new: small read-only functions that return plain dicts; no HTTP)   ui/bench_page.py
                    |                                                                |
                    v                                                                v
   ui/server.py (new: stdlib http.server on 127.0.0.1, GET only)      docs/benchmark/index.html
                    |                                                   (one offline file)
                    v
   ui/web/  (static HTML, CSS, JS, fonts; shipped inside the wheel)
```

- `ui/api.py` has the data layer, and that's what gets unit-tested. Each endpoint is one function:
  `overview(root)`, `checks(root)`, `check(root, id)`, `graph(root, id)`, `node(root, id, node_id)`,
  `benchmark(root)`. They call `analysis/` and `index/store.py` and return sorted dicts.
- `ui/server.py` is a thin layer: it maps URLs to `api.py` functions and serves files from `ui/web/`.
  No logic beyond that.
- `ui/web/` is the frontend. It talks only to `/api/*` on the same origin.

## 2. Tech choices, with reasons

| choice | why | what we gave up |
|---|---|---|
| **Python stdlib `http.server`** (`ThreadingHTTPServer`) | No new runtime dependency. We need about 6 read-only GET endpoints on localhost. | No async, no websockets. Not needed: the data doesn't change while the page is open. |
| **Plain JS (ES modules), no framework, no build step** | Same approach as the existing `--html` report. No Node needed to install, build or run AeroTrace. Small (target under 150 KB of JS for the whole app). `// @ts-check` with JSDoc types for editor checks. | Hand-written DOM code for lists and panels. That's fine for 5 pages. If it gets painful, Preact (4 KB, vendored) is the fallback. It needs no build step either. |
| **Canvas 2D for the graph** | 5,000 nodes and 10,000 edges as DOM or SVG elements would stutter. Canvas draws them in one pass, batched by colour. Measured target: at least 50 fps panning at 5,000 nodes on an M1 laptop, at least 30 fps on a 2018-era Linux laptop. | Hit-testing and text labels are our job (a simple grid index for clicks; labels only above a zoom level). Screen readers can't see canvas, so every node is also in an accessible list (same as the existing report). |
| **WebGL** (not now) | Only if canvas misses the fps target at 5,000+ nodes in Phase 4 measurements. | Extra code. We'll decide on measured numbers. |
| **Layout: rings by distance, no physics** | The brief asks for rings around the changed function. A ring layout is fast (O(n log n)), and it's **deterministic**: same check, same picture. Force layouts move every time and cost a lot at 5,000 nodes. | Ring layouts can get crowded. We collapse far rings (see 4.3) and sort each ring by parent angle so edges stay short. |
| **Fonts: bundled, OFL** | Brief: no web fonts from the internet. To get the same look on macOS, Linux and in screenshots, we bundle subsets: **Inter** (UI) and **JetBrains Mono** (numbers, code, file:line), woff2, about 120 KB total. Both use the SIL Open Font License. | About 120 KB in the wheel. **Decided: bundle them.** Licence files ship next to the fonts. |
| **Playwright for browser tests** | Already used for the `browser` marker (`uv run --with playwright`). It stays a dev-only tool, not a runtime dependency. | Downloads a browser once per dev machine and CI runner. |

Nothing here adds a runtime dependency. Dev-only: Playwright (already used), and for the benchmark,
`clangd` (ships with Xcode and LLVM) and `ripgrep`.

## 3. How the dashboard reads data

### 3.1 The index

`api.overview()` opens `.aerotrace/index.db` read-only (`file:...?mode=ro` URI) through
`index/store.py`. It reads:

- files: count of `files` rows, split into `is_unit` (parsed) and headers.
- functions, variables, edges: row counts.
- blind spots: `blind_spots` grouped by `kind`, with counts.
- last indexed: the index file's modification time, shown as a date. It's display-only, so it doesn't
  touch the determinism rule for analysis answers. (The `meta` table has no build date, on purpose.)
- coverage line ("checked X of Y files in this build"): reuses `build_coverage()`, which is being added
  on `feat/check-output-polish`. **That branch must merge first.** Otherwise the Overview page has no
  coverage line, and I'll show "coverage needs AeroTrace 0.1.0 or newer" rather than make up a number.

No schema change. No `SCHEMA_VERSION` bump.

### 3.2 A missing, old or broken index

| state | what the page shows |
|---|---|
| no `.aerotrace/` | empty state: "No index yet. Run: `aerotrace index`" |
| index from an older schema | error state: "This index was made by an older AeroTrace. Run: `aerotrace index`" |
| index can't be opened | error state with the reason from SQLite and the same fix |
| index is out of date (files changed since) | a warning bar from `aerotrace status`'s logic: "12 files changed since the last index. Run: `aerotrace index`" |

### 3.3 Saved checks (decided: a separate small PR after #55 and #56 merge)

Today `aerotrace check` prints its answer and keeps nothing. The Changes page needs past runs. Proposal:

- `aerotrace check` also saves its JSON (the existing `aerotrace-check` v1 contract, unchanged) to
  `.aerotrace/checks/<n>-<short-hash>.json`, where `n` counts up and the hash is from the JSON content.
- Next to it goes `<same>.run.json`, with what the dashboard needs that the contract mustn't hold:
  run date, source (`working tree`, `branch feat/x vs main`, or `patch file.diff`), and tool version.
  Keeping the date out of the check JSON keeps that JSON deterministic.
- Keep the last 50; delete older ones (only inside `.aerotrace/checks/`).
- `--no-save` turns it off. `aerotrace check` already writes into `.aerotrace/` (the index), so rule 6
  holds.

This is not a change to `store.py`'s SQLite schema. It's a new folder of plain files. It touches
`cmd_check`, so it's its own small PR (`feat/save-checks`), opened only after #55 and #56 merge. That PR
also tells users in the README to add `.aerotrace/` to their `.gitignore` (the index and saved checks are
local and machine-specific).

Until it lands, the dashboard can also open a check JSON you saved by hand
(`aerotrace check --json > .aerotrace/checks/mine.json`). Those show "date unknown" and "source unknown"
rather than a guess.

### 3.4 API (all `GET`, all JSON, all sorted)

| URL | returns |
|---|---|
| `/api/overview` | repo name (folder name only, no full path), index stats, coverage, blind-spot summary, last 5 checks |
| `/api/checks` | saved checks, newest first: id, date, source, verdict line, counts |
| `/api/checks/<id>` | the saved `aerotrace-check` JSON |
| `/api/checks/<id>/graph` | nodes with ring, angle order and collapsed groups, ready to draw (layout is computed in Python so it can be tested and is deterministic) |
| `/api/checks/<id>/node/<node_id>` | details: file:line, code snippet (up to 15 lines read from the file, the only source read), why, how sure, path back to the change |
| `/api/benchmark` | the latest `bench/results/*/results.json` if the repo has one, else `null` |

The code snippet is the only time the server reads a source file. It's read-only and limited to files
listed in the index, so a crafted URL can't read `/etc/passwd`.

## 4. The graph screen

### 4.1 Layout

- Ring 0: the changed, new and removed nodes (several changes sit on a small inner circle).
- Ring k: nodes at `distance` k. In each ring, order by the angle of the node's `via` parent, then by id.
  The same input always gives the same picture.
- Ring radius grows with node count so dots don't overlap (minimum 14 px gap at zoom 1).

### 4.2 Drawing

- Dark dot-grid background (in both themes; the graph canvas is always dark, the frame follows the theme).
- Nodes: the status shape and icon from `ui/html.py` `SHAPES` (BREAKS square with X, AFFECTED triangle
  with !, UNKNOWN circle with ?, CHANGED diamond). Colour comes from tokens. Shape and label, never colour
  alone.
- Edges: thin lines, `exact` solid, `probable` dashed, `unknown` dotted.
- Labels: drawn only when zoomed in enough, or for hovered, selected and BREAKS nodes.
- Zoom and pan: wheel or pinch zooms around the pointer; drag pans. `requestAnimationFrame`, only redraw
  when something moved.

### 4.3 Keeping big results readable

- **Default view (owner decision):** only CHANGED, BREAKS and MAY BREAK are drawn. AFFECTED, UNKNOWN and
  NO EFFECT FOUND start as counted groups ("19 AFFECTED"), each opened with one click or its filter chip.
  BREAKS and MAY BREAK are never collapsed. A hint says how many nodes are in groups.
- When an opened status has more than 300 nodes, rings beyond distance 3 collapse again into one bubble
  per ring ("143 more, distance 4").
- **Labels never overlap.** Each label tries up to 9 spots around its node (outward first) in priority
  order: selected, CHANGED, BREAKS, MAY BREAK, UNKNOWN, AFFECTED. A spot is taken only if it hits no other
  label, node, group bubble, legend or control. Labels that don't fit are hidden, and the canvas says how
  many ("12 labels hidden so none overlap. Zoom in to see them."). Hover always shows the name.
- Legend bottom left, with one short line per status. Zoom controls bottom right of the canvas. Group
  bubbles go in a row between them, or in a column above the zoom controls when the row doesn't fit.
- Focus mode (`F`): show only the selected node, its path to the change, and its direct neighbours.
- Filter chips: BREAKS, MAY BREAK, AFFECTED, UNKNOWN, NO EFFECT FOUND, plus "functions" and "data".
- Search (`/`): by name or file:line; picks the node and centres it.
- Fit to screen (`0`). The toolbar is one row that never wraps: the verdict shortens with an ellipsis and
  the chips drop their words (icon and count stay) below 1360 px.

### 4.4 Side panel (click a node)

file:line (copy button), code snippet with the linked lines marked, **why** (from the JSON `why`), **how
sure** (certainty plus the raw link label and what it means), and the **path** from the change as a list
of clickable steps.

## 5. Pages

| page | content | empty state |
|---|---|---|
| Overview | repo name, files / functions / last indexed, coverage line, blind spots summary, recent checks | "No index yet. Run: `aerotrace index`" |
| Changes | table of saved checks: verdict line, date, source; click opens Graph | "No checks yet. Run: `aerotrace check`" |
| Graph | 4.x above | "Pick a check on the Changes page." / "This check found nothing to draw." |
| Benchmark | the Phase 2 chart, from `results.json` | "No benchmark results in this repo. See docs/benchmark/RESULTS.md." |
| Memory, Track record | "Coming soon" card that says what it will do. No sample data. | n/a |

Layout: a collapsible left sidebar (logo, repo name, pages, theme toggle), main area, and a right panel
that slides in. Shortcuts: `/` search, `Esc` close panel, `g o` / `g c` / `g g` / `g b` go to pages, `?`
shows them all.

## 6. Launch and packaging

- `aerotrace ui [--root DIR] [--port N] [--no-open]`: starts the server on `127.0.0.1` on a free port
  (or `--port`), prints the URL, and opens the browser with `webbrowser.open` unless `--no-open`. `Ctrl+C`
  stops it.
- Security on localhost:
  - A random token is put in the URL on start (`http://127.0.0.1:PORT/?t=...`) and stored as a
    same-site cookie. API calls without it get 403. This stops other local web pages from reading your
    code through the server.
  - The `Host` header must be `127.0.0.1:PORT` or `localhost:PORT` (stops DNS rebinding).
  - CSP `default-src 'self'`, `X-Content-Type-Options: nosniff`, no CORS headers.
  - The token is the only random thing, and it never appears in any answer.
- Files: `src/aerotrace/ui/web/` is already inside `packages = ["src/aerotrace"]`, so hatchling ships it.
  A test checks the built wheel contains `ui/web/index.html` and the fonts.
- Fresh install: `uv tool install aerotrace` (or `pipx install aerotrace`), then `aerotrace ui`. No Node
  and no network after install. Phase 4 tests this in clean macOS and Linux (Docker) environments.

## 7. Benchmark (Phase 1 outline; details come with the case list)

- **Repos:** pinned Redis 8.10.3 and SQLite 3.54.1 (already in `tests/realrepo/repos.py`), plus **curl**
  (a pinned release tarball with a checked sha256, added the same way). The curl build needs a
  `compile_commands.json`. I'll write it the way the others are written (flags read from its Makefile).
- **Cases:** 20 to 30 real changes, mixing easy (direct calls) and hard (function pointers, struct fields,
  globals, removed functions, macros). Each case is a small patch in `bench/cases/<repo>/<id>.diff` with a
  one-line reason. The list is committed before any tool runs.
- **Truth:** apply the patch and compile the affected files with the repo's flags
  (`clang -fsyntax-only` per unit, the same flags as the index, `-ferror-limit=0`). Every compiler error
  is a "breaks" truth entry with file:line and the error text. "Might break" entries are checked by hand
  against the source, with the lines that prove them. Truth never comes from AeroTrace's output.
- **Tools**, each run the normal way:
  - AeroTrace: `aerotrace check --patch <case.diff> --json`.
  - grep: `rg -w <changed names>` over the repo (what an agent does first).
  - clangd: find-references on each changed symbol through a small LSP client script (stdlib, JSON-RPC
    over stdin/stdout), using the same `compile_commands.json`.
  - cscope (decided): its "functions calling this function" query (`cscope -d -L3 <name>`), on a
    database built with `cscope -b -q -k` over the same files the compile database lists.
- **Measures:** recall (truth breaks found), precision (answers that are real), wall time (median of 3
  runs), and tokens an agent would read. Tokens (decided): Anthropic's token-counting endpoint, **in the benchmark
  only, never in the product**. Each count is cached in `bench/results/<run>/tokens.json` keyed by the
  sha256 of the text, so re-scoring and the results page work offline. The page labels them "Claude
  tokens, counted with the API on <date>".
- **One command (decided: dev-only):** `uv run python -m bench accuracy`, in `bench/`. Not in the
  shipped CLI or the wheel.
- **Output:** `bench/results/<YYYY-MM-DD>-<commit>/` with `results.json` (date, machine, OS, CPU,
  tool versions, AeroTrace commit), and `raw/` with every tool's raw output.
- **Cost:** no model calls in Part A, so $0 apart from token counting (free). The $25 agent pilot is
  separate and is not part of this project unless you ask.

## 8. Owner decisions (2026-11-24)

1. Rule 7 is now "only a local, offline, read-only UI", with ADR 0008. Done in this PR.
2. I commit on my own feature branches (never `main` or `launch-candidate`), rebase before each PR, and
   run a reviewer agent on every PR. The owner merges.
3. Inter and JetBrains Mono are bundled (OFL).
4. Saved checks in `.aerotrace/checks/`: yes, as a separate small PR after #55 and #56 merge. The README
   tells users to gitignore `.aerotrace/`.
5. cscope is the third tool.
6. Anthropic token counting in the benchmark only, with cached counts. Never in the product.
7. The benchmark command is dev-only, in `bench/`.
8. Keep all four status words. The legend gives one short line each, for example "MAY BREAK: may not
   build; one step is a guess" and "AFFECTED: builds, but behaviour may change".
9. OpenUI stays unused. The `frontend-design` skill isn't installed; the design system is hand-written,
   guided by `docs/design/REFERENCES.md`.

### Sample numbers never reach the launch folder

Mockups show sample numbers, so:

- Every mockup page loads `mock.js`, which adds the "MOCKUP · sample numbers, not results" banner and
  marks the page with `data-sample="true"`.
- The real screenshot script (Phase 4) refuses to capture any page with `data-sample`, and writes
  `docs/launch/screenshots/MANIFEST.json` listing each image with the `results.json` sha256 it came from.
- `tests/ui/test_launch_screenshots.py` fails if any image in `docs/launch/screenshots/` is missing from
  that manifest, if a manifest entry points at a mockup, or if any mockup page lacks the banner script.

## 9. Folder layout (new and changed)

```
src/aerotrace/ui/
  api.py              # data layer (new), unit-tested
  server.py           # localhost GET server (new), thin
  bench_page.py       # results.json -> docs/benchmark/index.html (new)
  web/
    tokens.css        # THE design tokens (new; report.css will move onto it later)
    app.css  index.html
    js/  app.js  router.js  api.js  graph/  pages/  components/
    fonts/            # Inter, JetBrains Mono subsets + OFL.txt
docs/design/          # design system: README.md, components.html
docs/design/mockups/  # Phase 0 low-fi mockups (+ screenshots/)
docs/benchmark/       # RESULTS.md, index.html (generated)
bench/cases/          # case patches + cases.json (Phase 1)
bench/accuracy/       # harness (exists; extended), dev-only, run with uv run python -m bench
bench/results/<run>/  # results.json + raw/
tests/ui/             # api tests, server tests, browser + screenshot tests
```

## 10. Test plan

- **Data layer** (`tests/ui/test_api.py`, default run, no browser): every endpoint on the tiny C fixtures
  (known answers), with an empty repo, an old index, a broken index, and a check with 5,000 nodes. Same
  input, same bytes. Graph layout is deterministic, and BREAKS nodes are never collapsed.
- **Server** (`tests/ui/test_server.py`): GET only (POST gives 405), token required, Host check, path
  traversal refused, snippet only from indexed files, CSP header present, and no request leaves
  127.0.0.1 (checked by blocking sockets in the test).
- **Browser** (`-m browser`, Playwright): open each page on a recorded Redis demo check, keyboard-only
  walk through every page, Esc closes the panel, `/` focuses search, no network request outside the
  origin (Playwright request log), and no console errors.
- **Screenshots** (`-m browser`): each page in light and dark, compared with goldens at a small
  pixel-diff threshold. Run on Linux in CI only (fonts are bundled, so they match).
- **Performance:** a synthetic check with 1,000 and 5,000 nodes. Record first-paint time and fps during a
  scripted pan and zoom, and report the numbers (Phase 4).
- **Accessibility:** axe-core (vendored, dev-only) on each page, plus checks that contrast is at least
  4.5:1 for text, computed from `tokens.css` in a unit test.
- **Results page:** it's generated twice and the bytes must match. Every number on it must appear in
  `results.json` (the test parses the page). It makes no network request.

## 11. Risks

| risk | what we do |
|---|---|
| Canvas misses the fps target at 5,000+ nodes | Measure in Phase 4. Batch by colour, skip labels when zoomed out, cull off-screen nodes. WebGL as a fallback. |
| The benchmark looks rigged (we picked cases we win) | Case list and reasons are committed first, for your approval. Hard cases include known AeroTrace gaps (macros, #52). Losses and ties are reported. Every tool runs its normal way, and the exact commands are in the footnote. |
| Compiler truth misses runtime-only breaks (a pointer table called with a new meaning) | Those are "might break" truth, hand-checked with source lines, kept separate from compiler truth. Both are shown. |
| clangd or cscope set up badly (unfair to them) | Use their documented defaults with the same `compile_commands.json`. Write the setup in the footnote, and invite corrections. |
| curl's build flags are hard to get without running its build | Read them from its Makefile or CMake files by hand, like Redis today. If that fails, drop curl and say so (rule 6: we never run their build for the user, though building the *benchmark's* pinned copy in a temp folder is fine). |
| A localhost server is an attack surface | Token, Host check, GET only, files limited to the index, CSP; tests for each. |
| Design drifts between report, results page and dashboard | One `tokens.css`. Phase 4 moves `report.css` onto it, with a check that nothing else defines colours. |
| Saved checks fill the disk | Keep the last 50 (they're a few KB to a few MB each). |
| Old `--html` report and the new dashboard disagree | Both read the same v1 JSON. The dashboard reuses `html.py`'s `SHAPES` table and `schema.node_word`. |

## 12. Working next to the other session

Another session is merging PRs into `launch-candidate` at the same time. So:

- My branches go in `docs/COORDINATION.md` (owner: "dashboard session") as soon as that file exists.
  It doesn't exist yet (checked 2026-11-24).
- Before each PR: fetch `launch-candidate` and rebase on it.
- I only touch dashboard, benchmark, UI and design files: `src/aerotrace/ui/`, `docs/design/`,
  `docs/benchmark/`, `docs/plans/dashboard-and-benchmark.md`, `bench/`, `tests/ui/`, and a new
  `aerotrace ui` / `aerotrace bench` entry in `cli.py`, kept to a few lines.
- **Changes I need in code others own, which I'll ask for rather than make:**
  - Saving check runs (3.3) changes `aerotrace check` (`cli.py` `cmd_check`, maybe `analysis/check.py`).
  - The coverage line (3.1) needs `build_coverage()` from `feat/check-output-polish` (`analysis/graph.py`,
    `analysis/check.py`). That's uncommitted work in the main checkout, not on any remote branch.
  - Any analysis fix the benchmark turns up becomes an issue for you, not an edit by me.

## 13. Phases and stops

0. This plan, design system and mockups. **Stop.**
1. Case list. **Stop.** Then truth, tool runs, RESULTS.md. **Stop.**
2. Results page. **Stop** with screenshots.
3. Dashboard, one page at a time. **Stop** after each, with screenshots.
4. Polish, QA, performance numbers, skeptic review, final screenshots. **Stop** with a report.
