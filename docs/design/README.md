# AeroTrace design system

One design for the dashboard (`aerotrace ui`), the benchmark results page and the `--html` report.
The tokens live in one file: [`src/aerotrace/ui/web/tokens.css`](../../src/aerotrace/ui/web/tokens.css).
Nothing else defines a colour, font size, spacing step, radius, shadow or duration.

See it: open [`components.html`](components.html) (all components, light and dark) and the page mockups
in [`mockups/`](mockups/). Screenshots are in `mockups/screenshots/`.

## Feel

Calm and plain. A tool for careful engineers checking risky changes, so: lots of space, few colours,
no decoration. Colour is kept for meaning (status, and AeroTrace's own results). Everything else is
neutral grey.

## Colour

| token | light | dark | use |
|---|---|---|---|
| `--accent` | `#3451d1` | `#8ea2ff` | AeroTrace blue: primary buttons, links, selected items, AeroTrace's bars, changed nodes |
| `--bg` / `--surface` / `--surface-2` | warm off-white / white / light grey | near-black steps | page / cards / table headers, hover |
| `--text` / `--text-2` / `--text-3` | 17:1 / 8.3:1 / 5.1:1 on `--bg` | 16.5:1 / 9.8:1 / 6.7:1 | body / secondary / captions (smallest allowed) |

### Status (fixed meaning, never reused for anything else)

Colour is **never** the only signal. Every status always has its **shape, icon and word**, also in
tables, chips, the legend and the graph. Words come from `ui/schema.py` `node_word()`.

| word | colour | shape + icon | meaning |
|---|---|---|---|
| **BREAKS** | red `--breaks` | square with ✕ | will not build or link; the compiler resolved every step (`exact`) |
| **MAY BREAK** | amber `--may` (solid) | square with ✕, dashed edge | will not build or link, but one step is a guess (`probable`) |
| **AFFECTED** | amber `--affected` (outline) | triangle with ! | uses changed code or data, so behaviour may change |
| **UNKNOWN** | grey `--unknown` | circle with ? | the effect could not be followed (pointer escapes, unindexed code) |
| NO EFFECT FOUND | dim grey `--no-effect` | small dot with – | checked, nothing found. **Never** say "safe". |
| CHANGED / NEW / REMOVED | accent | diamond with ■ / + / ✕ | part of the change itself |

Green (`--ok`) is used only for "the check finished with nothing to report" banners, never on a node.
That keeps green from reading as "safe".

Contrast, measured from `tokens.css` (WCAG 2.1): every text token is at least **4.8:1** on `--bg`,
`--surface` and `--surface-2` in both themes; status text on its `-soft` chip background is at least
**5.1:1**; chart bar edges and graph marks are at least **3:1** against their background (non-text rule).

In the benchmark chart AeroTrace is the only solid bar (accent). Other tools are light grey fills with a
darker outline (`--chart-other-*`), so blue is clearly the strongest bar. The exact value sits above every
bar in mono, and a short tool name sits under it, so the chart never depends on telling greys apart.
The check script will become `tests/ui/test_tokens.py`.

The graph canvas is **always dark** (`--graph-*`), in both themes, so dots and lines read the same way in
every screenshot.

## Type

- **Inter** for UI text. **JetBrains Mono** for every number, code snippet, file:line, id and command.
  Both are bundled as woff2 subsets (pending owner decision 3); system fonts are the fallback.
- Scale: 12 / 13 / 15 / 18 / 24 / 32 px. Body is 15, tables 13.
- Numbers use `font-variant-numeric: tabular-nums` so columns line up.
- Small caps labels (`RECALL · FALSE ALARMS · TIME · TOKENS`): 12 px, uppercase, `letter-spacing: 0.08em`,
  `--text-3`.

## Spacing, radius, shadow, motion

- Spacing: 4 px base. 4, 8, 12, 16, 24, 32, 48, 64, 96. Cards pad 24; page margins 32 (16 on phones).
- Radius: 4 (chips, inputs), 8 (buttons, cards), 12 (panels), full (toggles, dots).
- Shadow: three steps. Cards use 1, popovers and tooltips 2, the side panel 3. Dark theme leans on
  borders more than shadows.
- Motion: 120 ms (hover), 200 ms (panel, toggle), 320 ms (graph fit). One easing curve. All motion is
  turned off under `prefers-reduced-motion`.

## Components

All shown in [`components.html`](components.html):

- **Buttons:** primary (accent fill), secondary (border), ghost (text only), icon button (with an
  `aria-label`). Heights 32 / 36. Focus is a 2 px `--focus` ring with a 2 px offset, always visible on
  keyboard focus.
- **Chips:** status chips (icon + word + count) and filter chips (toggle, `aria-pressed`).
- **Toggle:** a switch for "Show other tools" and the theme (`role="switch"`, `aria-checked`).
- **Table:** sticky header, 13 px, mono numbers right-aligned, row hover, whole row clickable with a
  real link inside for keyboard users.
- **Side panel:** slides in from the right (420 px, full width on phones), `Esc` closes, focus moves into
  it and back out.
- **Legend:** shape + icon + word for every status shown, in the graph's bottom left.
- **Tooltip:** short text, 12 px, appears after 300 ms on hover or on focus, never holds the only copy of
  a fact.
- **Empty state:** what's missing, and the exact command that fixes it, in mono with a copy button.
  Example: "No checks yet. Run: `aerotrace check`".
- **Error state:** what went wrong in plain words, the fix, and the raw reason in a collapsed details box.
- **Loading state:** a quiet skeleton (grey blocks where content will be) after 150 ms; no spinner for
  quick loads.

## Writing

Plain, short English, the same as the CLI. Say what AeroTrace could not see. Never say "safe", "no
risk" or "guaranteed". Numbers are exact (no "~"), and anything left out says how many.
