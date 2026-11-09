"""The HTML report in a real headless browser. Skipped by default; run with:

    uv run --with playwright pytest -m browser tests/ui

It uses Playwright's own Chromium if installed (`playwright install chromium`), else a local Google
Chrome (`channel="chrome"`). No network is needed: pages are opened from file:// and every request the
page tries to make is recorded and must be empty.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import samples

from aerotrace.ui import html as ui_html

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


def _open(browser, tmp_path: Path, data: dict, name: str):
    path = tmp_path / f"{name}.html"
    path.write_text(ui_html.render(data), encoding="utf-8")
    page = browser.new_page()
    requests: list[str] = []
    errors: list[str] = []
    page.on("request", lambda r: requests.append(r.url))
    page.on("console", lambda m: errors.append(m.text) if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(path.as_uri())
    page.wait_for_selector(
        "html[data-render-ms]", state="attached"
    )  # wait_for_function needs eval; the CSP blocks it
    return page, requests, errors, path


def test_hostile_names_run_nothing_and_load_nothing(browser, tmp_path: Path) -> None:
    page, requests, errors, path = _open(browser, tmp_path, samples.hostile(), "hostile")
    page.evaluate(
        "() => document.querySelectorAll('#nodes g.node, #list button').forEach(e => {"
        "e.dispatchEvent(new MouseEvent('mouseover', {bubbles: true}));"
        "e.dispatchEvent(new MouseEvent('click', {bubbles: true})); })"
    )
    page.keyboard.press("/")
    page.keyboard.type("script")
    page.keyboard.press("Enter")
    assert page.evaluate("() => window.__pwned") is None
    assert requests == [path.as_uri()]
    assert errors == []
    page.close()


def test_keyboard_search_focus(browser, tmp_path: Path) -> None:
    page, _, errors, _ = _open(browser, tmp_path, samples.sample(), "sample")
    page.focus("#graph")
    page.keyboard.press("Home")
    assert page.text_content("#announce").startswith("CHANGED: ")
    page.keyboard.press("ArrowRight")
    active = page.get_attribute("#graph", "aria-activedescendant")
    assert active and active.startswith("n")
    page.keyboard.press("f")
    assert page.text_content("#status").startswith("Focus: ")
    assert "hidden" in page.text_content("#listnote")
    page.keyboard.press("Escape")
    assert page.text_content("#status").startswith("Showing ")
    page.keyboard.press("/")
    page.keyboard.type("server.c")
    page.keyboard.press("Enter")
    assert "match" in page.text_content("#status")
    assert page.text_content("#details h2")
    assert errors == []
    page.close()


@pytest.mark.parametrize(("n", "budget_ms"), [(1000, 300), (5000, 1000)])
def test_render_time(browser, tmp_path: Path, n: int, budget_ms: int) -> None:
    page, _, errors, _ = _open(browser, tmp_path, samples.big(n), f"big{n}")
    ms = int(page.get_attribute("html", "data-render-ms"))
    t = page.evaluate(
        "async () => { const t = performance.now(); document.getElementById('expand').click();"
        "await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));"
        "return performance.now() - t; }"
    )
    print(f"{n} nodes: first render {ms} ms, expand all {t:.0f} ms")
    assert ms <= budget_ms
    assert t <= 3 * budget_ms
    assert errors == []
    page.close()
