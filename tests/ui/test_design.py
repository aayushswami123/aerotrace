"""Design system rules that can be checked without a browser.

- Text colours in tokens.css meet WCAG 4.5:1 on every background, in both themes.
- Nothing in the web UI or the design files loads from the internet.
- Every mockup is marked as sample data, and no image in docs/launch/screenshots/ can come from one.
"""

from __future__ import annotations

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
    "ok",
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


REMOTE = re.compile(
    r"""(?:src|href)\s*=\s*["']\s*(?:https?:)?//|url\(\s*["']?\s*(?:https?:)?//|@import\s+(?:url\()?\s*["']?\s*(?:https?:)?//""",
    re.I,
)


def _web_files() -> list[Path]:
    out = [p for p in WEB.rglob("*") if p.suffix in {".html", ".css", ".js"}]
    out += [p for p in DESIGN.rglob("*") if p.suffix in {".html", ".css", ".js"}]
    return sorted(out)


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
    for url in re.findall(r'url\("(fonts/[^"]+)"\)', css):
        assert (WEB / url).is_file(), url
    assert (fonts / "Inter-LICENSE.txt").is_file()
    assert (fonts / "JetBrainsMono-OFL.txt").is_file()


def test_every_mockup_is_marked_as_sample() -> None:
    pages = [*sorted(MOCKUPS.glob("*.html")), DESIGN / "components.html"]
    assert pages
    missing = [
        p.name
        for p in pages
        if 'src="mock.js"' not in p.read_text("utf-8") and 'src="mockups/mock.js"' not in p.read_text("utf-8")
    ]
    assert not missing, "these pages don't load mock.js (banner + data-sample): " + ", ".join(missing)
    js = (MOCKUPS / "mock.js").read_text("utf-8")
    assert 'setAttribute("data-sample", "true")' in js
    assert "MOCKUP · sample numbers, not results" in js


def test_launch_screenshots_never_come_from_mockups() -> None:
    """Every launch image must be listed in MANIFEST.json, made from a real results.json, not a mockup."""
    if not LAUNCH_SHOTS.exists():
        return
    images = sorted(
        p.name for p in LAUNCH_SHOTS.iterdir() if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
    )
    if not images:
        return
    manifest_path = LAUNCH_SHOTS / "MANIFEST.json"
    assert manifest_path.is_file(), "docs/launch/screenshots/ has images but no MANIFEST.json"
    entries = {e["file"]: e for e in json.loads(manifest_path.read_text("utf-8"))["images"]}
    for name in images:
        assert name in entries, f"{name} is not in MANIFEST.json"
        e = entries[name]
        assert "mockups" not in e["page"] and "components.html" not in e["page"], (
            f"{name} was taken from a mockup"
        )
        assert re.fullmatch(r"[0-9a-f]{64}", e.get("data_sha256", "")), f"{name} has no data_sha256"
