"""Design system rules that can be checked without a browser.

- Text colours in tokens.css meet WCAG 4.5:1 on every background, in both themes.
- Nothing in the web UI or the design files loads from the internet.
- Every mockup is marked as sample data, and no image in docs/launch/screenshots/ can come from one.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
TOKENS = ROOT / "src" / "aerotrace" / "ui" / "web" / "tokens.css"
WEB = ROOT / "src" / "aerotrace" / "ui" / "web"
DESIGN = ROOT / "docs" / "design"
MOCKUPS = DESIGN / "mockups"
LAUNCH_SHOTS = ROOT / "docs" / "launch" / "screenshots"

TEXT = [
    "text",
    "text-2",
    "text-3",
    "accent",
    "breaks",
    "may",
    "affected",
    "unknown",
    "no-effect",
    "changed",
]
BACKGROUNDS = ["bg", "surface", "surface-2"]
SOFT = {"breaks": "breaks-soft", "may": "may-soft", "affected": "affected-soft", "unknown": "unknown-soft"}
GRAPH_MARKS = ["graph-label", "graph-breaks", "graph-may", "graph-affected", "graph-unknown", "graph-changed"]


def _block(css: str, selector: str) -> dict[str, str]:
    m = re.search(re.escape(selector) + r"\s*\{(.*?)\n\}", css, re.S)
    assert m, f"{selector} block not found in tokens.css"
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})\b", m.group(1)))


def _themes() -> dict[str, dict[str, str]]:
    css = TOKENS.read_text(encoding="utf-8")
    light = _block(css, ":root")
    return {"light": light, "dark": {**light, **_block(css, '[data-theme="dark"]')}}


def _lum(hex_: str) -> float:
    def ch(i: int) -> float:
        c = int(hex_[i : i + 2], 16) / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * ch(1) + 0.7152 * ch(3) + 0.0722 * ch(5)


def _ratio(a: str, b: str) -> float:
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_text_contrast(theme: str) -> None:
    t = _themes()[theme]
    bad = [
        f"{fg} on {bg}: {_ratio(t[fg], t[bg]):.2f}"
        for fg in TEXT
        for bg in BACKGROUNDS
        if _ratio(t[fg], t[bg]) < 4.5
    ]
    bad += [
        f"{fg} on {bg}: {_ratio(t[fg], t[bg]):.2f}" for fg, bg in SOFT.items() if _ratio(t[fg], t[bg]) < 4.5
    ]
    if _ratio(t["accent-text"], t["accent"]) < 4.5:
        bad.append("accent-text on accent")
    assert not bad, "below 4.5:1: " + ", ".join(bad)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_chart_and_graph_marks_contrast(theme: str) -> None:
    t = _themes()[theme]
    assert _ratio(t.get("chart-1", t["accent"]), t["surface"]) >= 3
    assert _ratio(t["chart-other-stroke"], t["surface"]) >= 3
    for mark in GRAPH_MARKS:
        assert _ratio(t[mark], t["graph-bg"]) >= 4.5, mark
    assert _ratio(t["graph-no-effect"], t["graph-bg"]) >= 3


# Any way a page could pull something from another host: attributes (quoted or not, srcset too), CSS
# url() and @import, and JS fetch/import/XHR/EventSource/WebSocket/sendBeacon with an absolute URL.
_ABS = r"""(?:https?:|wss?:)?//[^\s"')]"""
REMOTE = re.compile(
    rf"""(?:\b(?:src|href|srcset|action|poster|data)\s*=\s*["']?\s*{_ABS})"""
    rf"""|(?:url\(\s*["']?\s*{_ABS})"""
    rf"""|(?:@import\s+(?:url\()?\s*["']?\s*{_ABS})"""
    rf"""|(?:\b(?:fetch|import|importScripts|EventSource|WebSocket|sendBeacon|open)\s*\(\s*["'`]\s*{_ABS})"""
    rf"""|(?:\bfrom\s+["']{_ABS})""",
    re.I,
)
WEB_SUFFIXES = {".html", ".css", ".js", ".mjs", ".svg"}


def _web_files() -> list[Path]:
    out = [p for p in WEB.rglob("*") if p.suffix in WEB_SUFFIXES]
    out += [p for p in DESIGN.rglob("*") if p.suffix in WEB_SUFFIXES]
    return sorted(out)


@pytest.mark.parametrize(
    "text",
    [
        '<script src="https://cdn.example/x.js">',
        "<img src=https://example.com/a.png>",
        '<img srcset="//example.com/a.png 2x">',
        '@import url("https://fonts.googleapis.com/css");',
        "background: url(//example.com/a.png)",
        'fetch("https://example.com/api")',
        "import x from 'https://esm.sh/x'",
        'import("https://example.com/m.js")',
        'new WebSocket("wss://example.com")',
    ],
)
def test_remote_pattern_catches(text: str) -> None:
    assert REMOTE.search(text)


@pytest.mark.parametrize(
    "text", ['<a href="#top">', 'url("fonts/a.woff2")', 'fetch("/api/overview")', "x // comment"]
)
def test_remote_pattern_allows_local(text: str) -> None:
    assert not REMOTE.search(text)


def test_nothing_loads_from_the_internet() -> None:
    files = _web_files()
    assert files
    hits = [
        f"{p.relative_to(ROOT)}: {m.group(0)}" for p in files for m in REMOTE.finditer(p.read_text("utf-8"))
    ]
    assert not hits, "remote loads: " + "; ".join(hits)


def test_fonts_are_bundled_with_licences() -> None:
    fonts = WEB / "fonts"
    css = TOKENS.read_text(encoding="utf-8")
    urls = re.findall(r'url\("(fonts/[^"]+\.woff2)"\)', css)
    assert len(urls) == 6, urls  # Inter and JetBrains Mono, 400 / 500 / 600
    for url in urls:
        assert (WEB / url).is_file(), url
    for name, copyright_ in (
        ("Inter-LICENSE.txt", "The Inter Project Authors"),
        ("JetBrainsMono-OFL.txt", "The JetBrains Mono Project Authors"),
    ):
        text = (fonts / name).read_text("utf-8")
        assert "SIL Open Font License, Version 1.1" in text and copyright_ in text, name
        assert "Reserved Font Name" not in text.split("PREAMBLE")[0], f"{name} reserves a font name"


_MOCK_SCRIPT = re.compile(r'<script\s+src="(?:mockups/)?mock\.js"\s*>\s*</script>')


def test_every_mockup_is_marked_as_sample() -> None:
    pages = [*sorted(MOCKUPS.glob("*.html")), DESIGN / "components.html"]
    assert len(pages) >= 8
    for p in pages:
        html = re.sub(
            r"<!--.*?-->", "", p.read_text("utf-8"), flags=re.S
        )  # a commented-out tag doesn't count
        assert _MOCK_SCRIPT.search(html), f"{p.name} doesn't load mock.js (sample strip, banner, data-sample)"
    js = (MOCKUPS / "mock.js").read_text("utf-8")
    assert 'setAttribute("data-sample", "true")' in js
    assert "MOCKUP · sample numbers, not results" in js
    assert "every number on this page is a made-up sample" in js


def test_mockup_screenshots_are_not_reused_elsewhere() -> None:
    """A mockup image copied anywhere else in the repo (README, launch, blog drafts) fails."""
    shots = MOCKUPS / "screenshots"
    mock = {hashlib.sha256(p.read_bytes()).hexdigest(): p.name for p in shots.glob("*.png")}
    assert mock
    reused = []
    for p in ROOT.rglob("*"):
        if p.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"} or shots in p.parents:
            continue
        if any(part in {".git", ".venv", "node_modules", ".claude"} for part in p.relative_to(ROOT).parts):
            continue
        if hashlib.sha256(p.read_bytes()).hexdigest() in mock:
            reused.append(str(p.relative_to(ROOT)))
    assert not reused, "mockup screenshots copied to: " + ", ".join(reused)


def test_launch_screenshots_never_come_from_mockups() -> None:
    """Every launch image must be in MANIFEST.json and name the real data it shows: a benchmark
    results.json in bench/results/, or a snapshot of the dashboard's answer saved in data/ next to it."""
    images = (
        sorted(
            p.name for p in LAUNCH_SHOTS.iterdir() if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
        )
        if LAUNCH_SHOTS.exists()
        else []
    )
    if not images:
        pytest.skip("no launch screenshots yet")
    manifest_path = LAUNCH_SHOTS / "MANIFEST.json"
    assert manifest_path.is_file(), "docs/launch/screenshots/ has images but no MANIFEST.json"
    entries = {e["file"]: e for e in json.loads(manifest_path.read_text("utf-8"))["images"]}
    real = {
        hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT / "bench" / "results").rglob("results.json")
    }
    for name in images:
        assert name in entries, f"{name} is not in MANIFEST.json"
        e = entries[name]
        assert "mockups" not in e["page"] and "components.html" not in e["page"], (
            f"{name} was taken from a mockup"
        )
        data = e.get("data", "")
        if data.startswith("data/"):  # a snapshot of what the dashboard answered for this image
            snap = LAUNCH_SHOTS / data
            assert snap.is_file(), f"{name}: {data} is missing"
            assert hashlib.sha256(snap.read_bytes()).hexdigest() == e.get("data_sha256"), (
                f"{name}: {data} changed"
            )
            assert "sample" not in snap.read_text("utf-8").lower()[:2000], (
                f"{name}: data looks like sample data"
            )
        else:
            assert e.get("data_sha256") in real, (
                f"{name}: data_sha256 matches no bench/results/*/results.json"
            )
