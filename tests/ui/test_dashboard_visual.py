"""Visual and accessibility checks of the dashboard, in light and dark, run in CI (pytest -m browser).

Visual: a layout and colour snapshot of each page (the boxes of the main parts, and their computed
colours) is compared with tests/golden/ui/dashboard-<page>-<theme>.json. Layout is compared with a few
pixels of slack, colours exactly. That catches broken layouts and wrong colours on macOS and Linux alike,
where pixel screenshots would differ by font rendering. Refresh the goldens with
AEROTRACE_UPDATE_GOLDEN=1 after a deliberate design change, and look at the diff.

Accessibility: every control has a name, the keyboard reaches every control, and keyboard focus is
visible.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
import samples
from test_dashboard_browser import browser as browser  # a fixture

from aerotrace.cli import main
from aerotrace.index import store
from aerotrace.ui import api, server

pytestmark = pytest.mark.browser

GOLDEN = Path(__file__).parents[1] / "golden" / "ui"
SLACK = 6  # px
PAGES = {
    "overview": (".stat", [".sidebar", ".page-head", ".card.stat", ".coverage", ".grid.g2"]),
    "changes": ("table", [".sidebar", ".page-head", ".tablewrap"]),
    "graph": (".stage canvas", [".gbar", ".gnotes", ".stage", ".legend", ".tools"]),
    "benchmark": ("table.bench", [".page-head", ".card.lead", ".tablewrap"]),
    "memory": (".state", [".state"]),
}
COLOURS = ["body", ".sidebar", ".card", ".chip.breaks", ".stage", ".legend", ".nav a[aria-current]"]


@pytest.fixture(scope="module")
def dash(tmp_path_factory) -> Iterator[server.DashboardServer]:
    from conftest import make_repo

    root = make_repo(tmp_path_factory.mktemp("dash"), "c_multi")
    assert main(["index", "--root", str(root), "--jobs", "1"]) == 0
    d = store.index_dir(root) / api.CHECKS_DIR
    d.mkdir(parents=True)
    (d / "0001-aaaa0001.json").write_text(json.dumps(samples.big(400)))
    (d / "0001-aaaa0001.run.json").write_text(
        json.dumps({"date": "2026-11-20T10:00+00:00", "source": "working tree vs HEAD"})
    )
    srv = server.DashboardServer(root)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def _open(browser, srv: server.DashboardServer, scheme: str, w: int = 1440, h: int = 900):
    ctx = browser.new_context(
        viewport={"width": w, "height": h}, color_scheme=scheme, reduced_motion="reduce"
    )
    pg = ctx.new_page()
    pg.goto(srv.url)
    pg.wait_for_selector("#main .page", timeout=15000)
    return ctx, pg


SNAP = """(sels) => {
  const box = (e) => { const r = e.getBoundingClientRect(); return [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]; };
  const out = { boxes: {}, colours: {} };
  for (const s of sels.boxes) { const e = document.querySelector(s); out.boxes[s] = e ? box(e) : null; }
  for (const s of sels.colours) {
    const e = document.querySelector(s);
    if (!e) continue;
    const c = getComputedStyle(e);
    out.colours[s] = [c.color, c.backgroundColor];
  }
  return out;
}"""


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("page", list(PAGES))
def test_layout_and_colours_match_the_golden(browser, dash, page: str, scheme: str) -> None:
    ready, boxes = PAGES[page]
    ctx, pg = _open(browser, dash, scheme)
    route = "graph/0001-aaaa0001" if page == "graph" else page
    pg.goto(dash.url.split("?")[0] + "#/" + route)
    pg.wait_for_selector(ready, timeout=15000)
    pg.wait_for_timeout(300)
    got = pg.evaluate(SNAP, {"boxes": boxes, "colours": COLOURS})
    ctx.close()
    path = GOLDEN / f"dashboard-{page}-{scheme}.json"
    if os.environ.get("AEROTRACE_UPDATE_GOLDEN") or not path.exists():
        path.write_text(json.dumps(got, indent=1, sort_keys=True) + "\n")
        if not os.environ.get("AEROTRACE_UPDATE_GOLDEN"):
            pytest.fail(f"wrote a new golden {path.name}; check it in")
        return
    want = json.loads(path.read_text())
    assert got["colours"] == want["colours"], "colours changed"
    for sel, b in want["boxes"].items():
        g = got["boxes"].get(sel)
        assert (b is None) == (g is None), f"{sel} appeared or disappeared"
        if b:
            assert all(abs(x - y) <= SLACK for x, y in zip(b, g, strict=True)), f"{sel} moved: {b} -> {g}"


NAMES = """() => [...document.querySelectorAll('button, a[href], input, [role=switch], canvas[tabindex]')]
  .filter(e => e.offsetParent !== null)
  .filter(e => !(e.getAttribute('aria-label') || e.textContent.trim() || e.getAttribute('title') || e.getAttribute('placeholder')))
  .map(e => e.outerHTML.slice(0, 120))"""


@pytest.mark.parametrize("route", ["overview", "changes", "graph/0001-aaaa0001", "benchmark"])
def test_every_control_has_a_name(browser, dash, route: str) -> None:
    ctx, pg = _open(browser, dash, "light")
    pg.goto(dash.url.split("?")[0] + "#/" + route)
    pg.wait_for_timeout(800)
    nameless = pg.evaluate(NAMES)
    ctx.close()
    assert nameless == []


def test_keyboard_reaches_every_control_with_visible_focus(browser, dash) -> None:
    ctx, pg = _open(browser, dash, "light")
    pg.goto(dash.url.split("?")[0] + "#/graph/0001-aaaa0001")
    pg.wait_for_selector(".stage canvas")
    pg.click("body", position={"x": 5, "y": 890})
    seen, unfocused = set(), []
    for _ in range(60):
        pg.keyboard.press("Tab")
        info = pg.evaluate("""() => { const e = document.activeElement, c = getComputedStyle(e);
          if (e === document.body) return { key: "", ring: true };  // Tab wrapping past the end, not a control
          return { key: (e.getAttribute('aria-label') || e.textContent || e.tagName).trim().slice(0, 40),
                   ring: c.outlineStyle !== 'none' && parseFloat(c.outlineWidth) > 0 }; }""")
        seen.add(info["key"])
        if not info["ring"]:
            unfocused.append(info["key"])
    ctx.close()
    for want in (
        "Overview",
        "Changes",
        "Graph",
        "Benchmark",
        "List",
        "Zoom in (+)",
        "Fit to screen (0)",
        "Impact graph. The list view has every node as text.",
    ):
        assert any(k.startswith(want[:40]) for k in seen), f"Tab never reached {want}"
    assert unfocused == [], f"no visible focus on: {sorted(set(unfocused))}"
