# Dashboard and results page: QA (Phase 4)

What was checked before launch, how, and what is still open. Numbers come from real runs on the
machine named under each one.

## Tests

| What | Where | Runs in CI |
|---|---|---|
| API answers: overview, checks, graph, node, blind spots, broken or missing index, no machine paths | `tests/ui/test_api.py`, `tests/ui/test_api_graph.py` | yes (`test`, Linux and macOS) |
| Server: 127.0.0.1 only, token to per-port cookie, Host/Origin/Sec-Fetch-Site checks, CSP, GET/HEAD only, no files outside `ui/web/` | `tests/ui/test_server.py` | yes (`test`) |
| Results page: built from the run, every number traced to `results.json`, wrong "safe" cases shown, CSP | `tests/ui/test_bench_page.py` | yes (`test`) |
| Design: contrast >= 4.5:1 in light and dark, no remote loads, fonts and licences, mockups marked as sample, launch screenshots never from mockups | `tests/ui/test_design.py` | yes (`test`) |
| Browser: every page loads with no console errors and no network beyond 127.0.0.1; labels never overlap; slow pages never replace the current one; Esc returns focus; via loops | `tests/ui/test_dashboard_browser.py` | yes (`browser`, Linux) |
| Visual: layout boxes and computed colours of 5 pages, light and dark, against `tests/golden/ui/` | `tests/ui/test_dashboard_visual.py` | yes (`browser`) |
| Wheel has every web file and font, and no `bench/`, `tests/`, `docs/` | `tests/ui/test_install.py` | yes (`test`) |
| Fresh install into a new venv serves `aerotrace ui` | `tests/ui/test_install.py` (realrepo) | yes (`realrepo`, Linux and macOS) |

Visual tests compare boxes and colours, not pixels. Font rendering differs between macOS and Linux, so
pixel diffs would fail for no reason; a moved panel or a wrong colour still fails.

## Performance

`bench/ui/perf.py`, synthetic checks of 1,000 and 5,000 nodes, Chrome, Apple M1 Pro
(`perf.json`). Two seconds of wheel zoom and drag pan per row.

| Nodes | View | First frame | Frame rate | Draw time avg / max |
|---|---|---|---|---|
| 1,000 | default (BREAKS, MAY BREAK) | 56 ms | 58.9 fps | 0.2 / 0.7 ms |
| 1,000 | every group on | 53 ms | 60.7 fps | 0.7 / 1.4 ms |
| 5,000 | default | 102 ms | 60.4 fps | 0.4 / 1.6 ms |
| 5,000 | every group on | 109 ms | 60.6 fps | 1.6 / 2.6 ms |

Before the fix, 5,000 nodes with every group on ran at 33-37 fps. The cause was dashed lines (measured
by turning them off). Lines off screen are now skipped, and over 600 dashed lines are drawn solid with a
note on screen saying so. Not measured: a slow laptop or Linux.

## Accessibility

- Every control has a name; checked on every page by `test_every_control_has_a_name`.
- The keyboard reaches every control with a visible focus ring (`test_keyboard_reaches_every_control_with_visible_focus`).
- Status chips carry their word for screen readers, not just a colour or icon.
- The graph canvas can be focused and says the list view has every node as text. The list view is the
  keyboard and screen-reader way through the graph.
- Text contrast is at least 4.5:1 in both themes (tested).
- Motion respects `prefers-reduced-motion`.

Not done: picking single nodes on the canvas with the keyboard (use the list view).

## Local only and read-only

- The server binds 127.0.0.1, answers GET and HEAD only, and serves files only from the packaged
  `ui/web/` folder. Source snippets come only from files in the index, resolved inside the repo.
- CSP `default-src 'none'`; fonts and scripts are bundled. The browser test fails on any request
  that leaves 127.0.0.1.
- The dashboard writes nothing. `aerotrace check` writes its saved answers to `.aerotrace/checks/` (#80).

## Packaging

The wheel is checked in CI. A fresh install into a new venv is tested on Linux and macOS in CI, and
locally on macOS. Nothing is fetched after install. Not tested: a Linux machine outside CI (no Docker here).

## Review

A skeptic review (written as a critical Hacker News reader) of the results page and the dashboard was
run before launch. It recomputed every per-case number from the raw answers and found them right, then
found where the page read better than the numbers. Fixed:

- The headline said AeroTrace's edge is few false alarms. On the 13 hard cases where AeroTrace named at
  least one break, it ties clangd exactly (91 of 91, 0 false alarms each); its lower count against clangd
  comes from cases where it named no break. The page and RESULTS.md now say "AeroTrace did not beat
  clangd here" (#74, #76; computed, with a test).
- Medians were the upper middle value, not the median (#74, #76).
- curl-09's answer opens with "Nothing found that this change can break" for a change that breaks 344
  functions. The red box now quotes it (#76).
- Caveats on the page: cases made by us, the link step ran in 4 of 27 cases, AeroTrace timed as a new
  process against a running clangd, cscope `-L0` not `-L3`. "Tokens" left the title strip until counted.
- `might_break.json` is now kept with the run, so the run folder holds all of its truth (#74).
- The demo checks in the launch screenshots had dates set by hand. They were redone with the real save
  from `aerotrace check` (#80), so every date on screen is real.
- A BREAKS node whose label had no room nearby went unnamed. BREAKS labels now try spots further out,
  with a thin line to the node (tested on a small check).
- Legend swatches looked like empty check boxes; they are bar-shaped now.

Not changed, and why:
- The other tools share one grey (the owner's design); each bar has its tool name under it.
- A cut-off verdict in the graph bar: the full text is on hover, and the chips next to it hold the counts.
- Analysis wording (a compile error listed under "couldn't see"; "2 steps" next to "depth 5") belongs to
  the analysis code, not the UI. Reported to the owner.
- New benchmark cases from project history (#65), a cscope `-L3` row and token counts need a new run.

## Open

- The benchmark run (`bench/results/2026-11-24-c7df917`) measured AeroTrace before #77, which fixes
  the 4 wrong "safe" answers. The page says they are not fixed in that run. A new run is needed to
  show the fix.
- Token counts are not on the page yet: they need Anthropic API access for the benchmark (cached).
- Functions/data filter chips on the graph, a dark theme for the results page: not built.
