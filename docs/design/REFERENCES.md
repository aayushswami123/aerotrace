# Design references

Inspiration only. We take ideas (spacing, density, where a label sits), never a product's exact design,
logo, illustrations or code. Nothing shipped loads anything from these sites: fonts are bundled (see
`src/aerotrace/ui/web/fonts/README.md`), and there are no remote images or scripts.

**One feel for everything:** calm, precise, lots of space, neutral greys, one blue accent, colour kept for
meaning, and a dark dot-grid graph canvas. When two references pull different ways, the calmer one wins.

## What was looked at (2026-11-24)

| source | how | what we could see |
|---|---|---|
| Refero (refero.design) | browser | The home page, the "Styles" list and the general screen feed are public. **Search needs a login, so it was skipped**, as the owner asked. The "Black with one accent" style matches our direction. |
| Supermemory research page (supermemory.ai/research) | browser | The benchmark chart: small-caps metric line, a "Show competitors" box, product name and value above each bar, grey bars for others, time and tokens under each group, an "Also run" row. |
| CTA Gallery (cta.gallery) | fetch | Public. Index only: categories, and entries from dev tools (Tines, Bannerbear). For a future landing page only. |
| Footer.design | fetch | Public. A filterable gallery ("Small type", "Typographic"). For the results page footer only. |
| Navbar Gallery (navbar.gallery) | fetch | Public. Logo on the left, 4 to 7 links, one CTA. For a future landing page only. |
| Linear, Vercel, Sentry, Raycast | **not fetched**: from general knowledge of their public product UI | Used for well-known patterns only (named below). Check the claims against your own screenshots. |
| Owner's picks (`docs/design/references/`) | not there yet | **The strongest signal once you add them.** I'll revise this file against them. |

## Per page

### Overview

| reference | idea taken | made our own by |
|---|---|---|
| Vercel project overview | A few big stat tiles, then one clear status line under them | Our tiles hold index facts in mono. The status line is the coverage sentence ("Checked 96 of 104 files"), which says what was *not* checked. |
| Linear | Quiet sidebar: small icons, one highlighted item, a muted section break before secondary items | Secondary items are the "soon" pages, with a plain "soon" pill and no fake content. |
| Sentry issue stream | Each recent item is one line of what happened plus a mono subline of where | The line is our verdict sentence; the chip carries icon, word and count. |

### Changes

| reference | idea taken | made our own by |
|---|---|---|
| Linear issue list | Dense rows (about 48 px), whole row clickable, a muted second line | The status column always shows icon + word, so the table reads in greyscale. |
| Sentry | A warning bar above a list when the data may be stale | Our bar names the exact fix (`aerotrace index`) and the count of changed files. |
| Vercel deployments list | Source of each run (branch, commit) in mono, date right-aligned | Unknown source or date shows "unknown", never a guess. |

### Graph

| reference | idea taken | made our own by |
|---|---|---|
| Refero "Black with one accent" | Near-black canvas, one strong accent, everything else grey | Status colours (red, amber) appear only on BREAKS and MAY BREAK, which is the point of the screen. |
| Sentry trace view and Raycast panels | A detail panel with stacked labelled sections (Why, Code, How sure, Path) and generous padding | Our sections answer AeroTrace's own questions, and the path is a clickable list of steps back to the change. |
| Linear | Keyboard first (`/`, `Esc`, `F`, `0`), with shortcuts shown in `kbd` chips | Same shortcuts work with no mouse at all, and the accessible list mirrors the canvas. |

### Benchmark (dashboard page)

| reference | idea taken | made our own by |
|---|---|---|
| Supermemory chart | The metric line in small caps above the chart, "show others" toggle | Smaller, inside a card, with a per-case table below. Every tool was run by us on the same machine, which the page says. |
| Vercel analytics tables | Mono numbers right-aligned, a "showing 3 of 28" line | Each cell shows found / false alarms, so no single number hides the other. |

### Results page

| reference | idea taken | made our own by |
|---|---|---|
| Supermemory chart | Value above each bar, time and tokens under each group, an "Also run" row, links to raw results | Other tools are **outlined light-grey bars** (not solid grey), and the chart has an Easy / Hard split. The page also has a "Wins / Ties / Losses" row so losses are as easy to find as wins. Every number is generated from `results.json`. |
| Supermemory, wording | They mark which bars come from other people's published figures | All our bars come from our own runs. The footnote says how each tool was run, so readers can say if we ran one badly. |
| Footer.design "Small type" | A quiet, dense footnote block in 12 px | Ours is method, versions, machine and the reproduce command, not links to a marketing site. |

### Empty, error, loading, and "coming soon"

| reference | idea taken | made our own by |
|---|---|---|
| Linear and Vercel empty states | One icon, one short reason, one action | The action is always the exact command with a copy button. Errors keep the raw reason in a closed box. "Coming soon" pages say they aren't built and show no sample data. |

## Not used

- 3D, parallax and scroll effects (Scrolltide style). They don't suit a fast, offline developer tool.
- Gradients, glass or blur except the graph overlays, which are blurred only so canvas lines don't show
  through the legend.
