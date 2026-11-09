"""The offline HTML report: golden snapshot, determinism, no network, escaping, size."""

from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest
import samples

from aerotrace.ui import html as ui_html
from aerotrace.ui.schema import STATUSES

GOLDEN = Path(__file__).parents[1] / "golden" / "ui"
ASSETS = Path(ui_html.__file__).parent / "assets"
SVG_NS = "http://www.w3.org/2000/svg"

CASES = {
    "check": samples.sample,
    "nothing": samples.nothing,
    "unclear": samples.unclear,
    "no_index": samples.no_index,
    "index_error": samples.index_error,
    "nothing_to_check": samples.nothing_to_check,
    "nothing_to_check_gaps": samples.nothing_to_check_gaps,
    "empty": samples.base,
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_golden_html(case: str) -> None:
    text = ui_html.render(CASES[case]())
    path = GOLDEN / f"{case}.html"
    if os.environ.get("UPDATE_GOLDEN"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    assert text.encode("utf-8") == path.read_bytes(), (
        f"{path.name} changed; rerun with UPDATE_GOLDEN=1 to accept"
    )


def _reorder(v: object) -> object:
    if isinstance(v, dict):
        return {k: _reorder(v[k]) for k in reversed(list(v))}
    if isinstance(v, list):
        return [_reorder(x) for x in v]
    return v


def test_deterministic() -> None:
    d = samples.sample()
    first = ui_html.render(d)
    assert ui_html.render(samples.sample()) == first
    assert ui_html.render(_reorder(d)) == first  # type: ignore[arg-type]
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); import samples; "
        "from aerotrace.ui.html import render; sys.stdout.buffer.write(render(samples.sample()).encode())"
    )
    outs = {
        subprocess.run(
            [sys.executable, "-c", code, str(Path(__file__).parent)],
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            check=True,
        ).stdout
        for seed in ("0", "1", "12345")
    }
    assert outs == {first.encode("utf-8")}


FORBIDDEN = [
    r"https?:",
    r"\bftp:",
    r"\bdata:",
    r"\bjavascript:",
    r"\burl\(",
    r"@import",
    r"<link\b",
    r"<img\b",
    r"<iframe\b",
    r"<object\b",
    r"<embed\b",
    r"<form\b",
    r"<base\b",
    r"\bsrc\s*=",
    r"srcset",
    r"xlink",
    r"\bfetch\(",
    r"XMLHttpRequest",
    r"WebSocket",
    r"EventSource",
    r"sendBeacon",
    r"\bimport\(",
    r"importScripts",
    r"\bWorker\(",
    r"window\.open",
    r"location\.(href|assign|replace)",
    r"navigator\.(?!clipboard\b)",
    r"innerHTML",
    r"outerHTML",
    r"insertAdjacentHTML",
    r"document\.write",
    r"\beval\(",
    r"new Function",
]


@pytest.mark.parametrize("case", ["check", "no_index", "empty"])
def test_no_external_urls(case: str) -> None:
    text = ui_html.render(CASES[case]())
    assert text.count(SVG_NS) == 1  # the one allowed string: the SVG namespace in report.js
    # The CSP hashes are base64 and may contain '//' by chance; they are checked in test_csp_hashes_and_order.
    scan = re.sub(r'<meta http-equiv="Content-Security-Policy"[^>]*>', "", text).replace(SVG_NS, "")
    for pat in FORBIDDEN:
        assert not re.search(pat, scan), f"found {pat!r} in the report"
    assert "//" not in scan
    for href in re.findall(r"""href\s*=\s*["']?([^"' >]*)""", scan):
        assert href.startswith("#"), href


def test_assets_have_no_line_comments_or_urls() -> None:
    for name in ("report.js", "report.css", "report.html"):
        text = (ASSETS / name).read_text(encoding="utf-8").replace(SVG_NS, "")
        assert "//" not in text, f"{name} has '//' (no line comments, no URLs)"


def _sha(text: str) -> str:
    return base64.b64encode(hashlib.sha256(text.encode()).digest()).decode()


def test_csp_hashes_and_order() -> None:
    text = ui_html.render(samples.sample())
    head = text.split("<head>", 1)[1]
    metas = re.findall(r"<meta [^>]*>", head)
    assert metas[0] == '<meta charset="utf-8">'
    assert metas[1].startswith('<meta http-equiv="Content-Security-Policy"')
    csp = html.unescape(re.search(r'content="([^"]*)"', metas[1]).group(1))
    js = re.search(r"<script>(.*?)</script>", text, re.S).group(1)
    css = re.search(r"<style>(.*?)</style>", text, re.S).group(1)
    assert f"script-src 'sha256-{_sha(js)}'" in csp
    assert f"style-src 'sha256-{_sha(css)}'" in csp
    for need in ("default-src 'none'", "base-uri 'none'", "form-action 'none'"):
        assert need in csp
    assert "unsafe" not in csp and "img-src" not in csp
    assert " style=" not in text  # inline style attributes would be blocked by the CSP


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict]] = []
        self.data_block: list[str] = []
        self._in_data = False

    def handle_starttag(self, tag, attrs):  # type: ignore[override]
        a = dict(attrs)
        self.tags.append((tag, a))
        self._in_data = tag == "script" and a.get("type") == "application/json"

    def handle_endtag(self, tag):  # type: ignore[override]
        if tag == "script":
            self._in_data = False

    def handle_data(self, data):  # type: ignore[override]
        if self._in_data:
            self.data_block.append(data)


def test_hostile_names_stay_text() -> None:
    d = samples.hostile()
    text = ui_html.render(d)
    p = _Parser()
    p.feed(text)
    assert [t for t, _ in p.tags if t == "script"] == ["script", "script"]
    assert not [t for t, _ in p.tags if t in ("img", "iframe", "object")]
    for _, attrs in p.tags:
        assert not [k for k in attrs if k.startswith("on")], attrs
    data = json.loads("".join(p.data_block))
    assert [n[0] for n in data["nodes"]] == [n["name"] for n in d["nodes"]]
    assert "&lt;/script&gt;&lt;script&gt;" in text  # escaped in the list
    assert "</script><script>window" not in text


def test_status_table_pairs_colour_with_shape_icon_and_word() -> None:
    assert set(ui_html.SHAPES) == set(STATUSES)
    assert all(shape and icon for shape, icon in ui_html.SHAPES.values())
    non_change = [ui_html.SHAPES[s] for s in ("breaks", "affected", "unknown", "no_effect")]
    assert len({shape for shape, _ in non_change}) == 4
    text = ui_html.render(samples.sample())
    for word in ("CHANGED", "BREAKS", "AFFECTED", "UNKNOWN", "NO EFFECT FOUND"):
        assert word in text.split('class="legend"', 1)[1]


def test_nothing_to_check_lists_skipped_parts() -> None:
    text = ui_html.render(samples.nothing_to_check_gaps())
    assert "h-clear" not in text and "✅" not in text
    assert "src/extra.c is not in the index" in text


def test_bidi_controls_are_shown_not_applied() -> None:
    d = samples.sample()
    d["nodes"][3]["name"] = "admin\u202eolleh"
    text = ui_html.render(d)
    assert "\u202e" not in text
    assert "admin\\u202eolleh" in text


def test_empty_states_have_no_graph_and_no_all_clear() -> None:
    for case in ("no_index", "index_error"):
        text = ui_html.render(CASES[case]())
        assert 'id="graph"' not in text
        assert "checked nothing" in text
        assert "h-clear" not in text and "✅" not in text


@pytest.mark.parametrize(("n", "budget_kb"), [(1000, 500), (5000, 2500)])
def test_size_budget(n: int, budget_kb: int) -> None:
    size = len(ui_html.render(samples.big(n)).encode("utf-8"))
    assert size <= budget_kb * 1024, f"{n} nodes: {size / 1024:.0f} KB > {budget_kb} KB"


def test_write_html(tmp_path: Path) -> None:
    out = tmp_path / "report.html"
    assert ui_html.write_html(samples.sample(), out, tmp_path).startswith("Wrote ")
    assert ui_html.write_html(samples.sample(), out, tmp_path).startswith("Replaced the existing HTML file ")
    src = tmp_path / "server.c"
    src.write_text("int main(void) { return 0; }\n")
    for bad in (src, tmp_path / "notes.txt", tmp_path / "report"):
        with pytest.raises(ValueError, match=r"must end in \.html"):
            ui_html.write_html(samples.sample(), bad, tmp_path)
    assert src.read_text() == "int main(void) { return 0; }\n"
    (tmp_path / ".aerotrace").mkdir()
    with pytest.raises(ValueError, match="belongs to the index"):
        ui_html.write_html(samples.sample(), tmp_path / ".aerotrace" / "r.html", tmp_path)
    with pytest.raises(ValueError, match="is a folder"):
        ui_html.write_html(samples.sample(), tmp_path, tmp_path / "elsewhere")
