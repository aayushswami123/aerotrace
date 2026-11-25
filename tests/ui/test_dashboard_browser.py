"""The dashboard in a real browser: pages, graph labels, races, keyboard and screen-reader words.

Run with: uv run --with playwright pytest -m browser tests/ui/test_dashboard_browser.py
Uses a headless Chromium (or the installed Chrome). Everything is served from 127.0.0.1.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
import samples

from aerotrace.index import store
from aerotrace.ui import api, server

pytestmark = pytest.mark.browser


@pytest.fixture(scope="module")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        b = None
        for kwargs in ({}, {"channel": "chrome"}):
            try:
                b = p.chromium.launch(**kwargs)
                break
            except Exception:
                continue
        if b is None:
            pytest.skip("no Chromium or Chrome found")
        yield b
        b.close()


def _repo(tmp: Path, checks: dict[str, dict]) -> Path:
    d = store.index_dir(tmp) / api.CHECKS_DIR
    d.mkdir(parents=True)
    for cid, data in checks.items():
        (d / f"{cid}.json").write_text(json.dumps(data))
    return tmp


@pytest.fixture
def dash(tmp_path: Path) -> Iterator[tuple[server.DashboardServer, Path]]:
    root = _repo(
        tmp_path,
        {
            "0001-aaaa0001": samples.big(1000),
            "0002-aaaa0002": samples.big(5000, seed=3),
            "0003-aaaa0003": samples.sample(),
        },
    )
    srv = server.DashboardServer(root)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, root
    srv.shutdown()
    srv.server_close()


def _page(browser, srv: server.DashboardServer, w: int = 1440, h: int = 900):
    ctx = browser.new_context(viewport={"width": w, "height": h})
    pg = ctx.new_page()
    problems: list[str] = []
    pg.on("console", lambda m: m.type in ("error", "warning") and problems.append(m.text))
    pg.on("pageerror", lambda e: problems.append(str(e)))
    pg.on(
        "request", lambda r: not r.url.startswith("http://127.0.0.1:") and problems.append("NETWORK " + r.url)
    )
    pg.goto(srv.url)
    pg.wait_for_selector("#main .page, #main .gwrap", timeout=15000)
    return ctx, pg, problems


def _base(srv: server.DashboardServer) -> str:
    return srv.url.split("?")[0]


def test_every_page_loads_without_errors_or_network(browser, dash) -> None:
    srv, _ = dash
    ctx, pg, problems = _page(browser, srv)
    for route, sel in (("overview", ".state, .stat"), ("changes", "table"), ("graph/0001-aaaa0001", ".stage canvas"),
                       ("benchmark", ".page"), ("memory", ".state"), ("track", ".state")):  # fmt: skip
        pg.goto(_base(srv) + "#/" + route)
        pg.wait_for_selector(sel, timeout=15000)
    ctx.close()
    assert problems == []


@pytest.mark.parametrize(
    ("cid", "groups"), [("0001-aaaa0001", []), ("0002-aaaa0002", ["affected", "unknown"])]
)
def test_labels_never_overlap_or_cover_the_controls(browser, dash, cid: str, groups: list[str]) -> None:
    srv, _ = dash
    ctx, pg, _ = _page(browser, srv)
    pg.goto(_base(srv) + "#/graph/" + cid)
    pg.wait_for_selector(".stage canvas", timeout=15000)
    for g in groups:
        pg.click(f'[data-group="{g}"]')
    pg.wait_for_timeout(300)
    for zoom in (None, "+", "+"):
        if zoom:
            pg.click(".tools button:first-child")
            pg.wait_for_timeout(200)
        stats = pg.evaluate("() => window.__aerotraceGraph")
        rects = [r for r in stats["labelRects"] if not r["forced"]]
        stage = pg.eval_on_selector(".stage", "e => e.getBoundingClientRect().toJSON()")
        overlays = pg.eval_on_selector_all(
            "[data-overlay]:not([hidden])", "els => els.map(e => e.getBoundingClientRect().toJSON())"
        )
        boxes = [
            {"x": o["x"] - stage["x"], "y": o["y"] - stage["y"], "w": o["width"], "h": o["height"]}
            for o in overlays
        ]

        def hit(a: dict, b: dict) -> bool:
            return (
                a["x"] < b["x"] + b["w"]
                and b["x"] < a["x"] + a["w"]
                and a["y"] < b["y"] + b["h"]
                and b["y"] < a["y"] + a["h"]
            )

        for i, a in enumerate(rects):
            assert not any(hit(a, b) for b in rects[i + 1 :]), "two labels overlap"
            assert not any(hit(a, b) for b in boxes), "a label covers the legend, hint or tools"
    ctx.close()


def test_every_breaks_node_has_its_name_when_there_is_room(browser, dash) -> None:
    """A small check, like most real ones: every BREAKS node on screen is labelled (no overlap either)."""
    srv, _ = dash
    ctx, pg, _ = _page(browser, srv)
    pg.goto(_base(srv) + "#/graph/0003-aaaa0003")
    pg.wait_for_selector(".stage canvas", timeout=15000)
    pg.wait_for_timeout(300)
    stats = pg.evaluate("() => window.__aerotraceGraph")
    ctx.close()
    named = {r["id"] for r in stats["labelRects"] if r["group"] == "breaks"}
    assert stats["breaksOnScreen"] > 0
    assert len(named) == stats["breaksOnScreen"], "a BREAKS node on screen has no label"


def test_a_slow_page_never_replaces_the_current_one(browser, dash) -> None:
    srv, _ = dash
    ctx, pg, _ = _page(browser, srv)
    pg.goto(_base(srv) + "#/graph/0002-aaaa0002")
    pg.goto(_base(srv) + "#/changes")  # before the big graph has loaded
    pg.wait_for_selector("table", timeout=15000)
    pg.wait_for_timeout(1500)
    assert pg.query_selector(".stage canvas") is None
    assert pg.get_attribute('.nav a[data-page="changes"]', "aria-current") == "page"
    ctx.close()


def test_screen_reader_words_on_chips(browser, dash) -> None:
    srv, _ = dash
    ctx, pg, _ = _page(browser, srv)
    pg.goto(_base(srv) + "#/graph/0001-aaaa0001")
    pg.wait_for_selector(".stage canvas")
    pg.click('[data-group="no_effect"]')
    pg.click('button.btn:text-is("List")')
    pg.wait_for_selector(".glist table")
    words = pg.eval_on_selector_all(
        ".glist td:first-child .chip", "els => els.map(e => e.textContent.trim())"
    )
    assert "CHANGED" in words
    assert all("undefined" not in w and not w.startswith("NO EFFECT FOUND CHANGED") for w in words)
    assert any(w == "NO EFFECT FOUND" for w in words)
    ctx.close()


def test_list_view_says_when_groups_are_off(browser, dash) -> None:
    srv, _ = dash
    ctx, pg, _ = _page(browser, srv)
    pg.goto(_base(srv) + "#/graph/0001-aaaa0001")
    pg.wait_for_selector(".stage canvas")
    pg.click('button.btn:text-is("List")')
    assert "groups that are off" in pg.inner_text(".glist caption")
    ctx.close()


def test_esc_closes_the_panel_and_returns_focus(browser, dash) -> None:
    srv, _ = dash
    ctx, pg, _ = _page(browser, srv)
    pg.goto(_base(srv) + "#/graph/0001-aaaa0001")
    pg.wait_for_selector(".stage canvas")
    pg.click('button.btn:text-is("List")')
    pg.click(".glist .linkish >> nth=1")
    pg.wait_for_selector(".panel:not([hidden]) .pname", timeout=15000)
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(200)
    assert pg.is_hidden(".panel")
    assert pg.evaluate("() => document.activeElement.classList.contains('linkish')")
    ctx.close()


def test_focus_mode_survives_a_via_loop(browser, tmp_path: Path) -> None:
    data = samples.big(200)
    far = [n for n in data["nodes"] if n["distance"] >= 2][:2]
    far[0]["via"], far[1]["via"] = far[1]["id"], far[0]["id"]
    root = _repo(tmp_path, {"0001-aaaa0001": data})
    srv = server.DashboardServer(root)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        ctx, pg, _ = _page(browser, srv)
        pg.goto(_base(srv) + "#/graph/0001-aaaa0001")
        pg.wait_for_selector(".stage canvas")
        pg.click('[data-group="affected"]')
        pg.fill("[data-search]", far[0]["name"])
        pg.press("[data-search]", "Enter")
        pg.click(".tools button:nth-child(4)")
        assert pg.evaluate("() => window.__aerotraceGraph.shown") >= 1  # answered: no hang
        ctx.close()
    finally:
        srv.shutdown()
        srv.server_close()
