"""The benchmark results page is generated from the run's JSON and never edited by hand."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from html import escape
from pathlib import Path

import pytest
from test_design import REMOTE

from aerotrace.ui import bench_page

ROOT = Path(__file__).parents[2]
RUN = ROOT / "bench" / "results" / "2026-11-24-c7df917"
PAGE = ROOT / "docs" / "benchmark" / "index.html"

pytestmark = pytest.mark.skipif(
    not (RUN / "results.json").exists(), reason="no benchmark run in this checkout"
)


@pytest.fixture(scope="module")
def data() -> dict:
    return bench_page.build(RUN)


@pytest.fixture(scope="module")
def html(data: dict) -> str:
    return bench_page.render(data)


def test_committed_page_is_the_generated_page(html: str) -> None:
    assert PAGE.read_text(encoding="utf-8") == html, (
        "docs/benchmark/index.html is stale: run bench/page/make_page.py"
    )


def test_same_run_same_bytes(data: dict) -> None:
    assert bench_page.render(data) == bench_page.render(bench_page.build(RUN))


def test_view_totals_match_the_scorer(data: dict) -> None:
    groups = json.loads((RUN / "results.json").read_text())["groups"]
    for view, key in bench_page.VIEW_GROUP.items():
        for tool in bench_page.TOOLS:
            ours, theirs = data["views"][view]["total"][tool], groups[key][tool]
            assert (ours["found"], ours["truth"], ours["false_alarms"]) == (
                theirs["found"],
                theirs["truth_sites"],
                theirs["false_alarms"],
            ), (view, tool)


def test_repo_rows_add_up_to_the_total(data: dict) -> None:
    for view in data["views"].values():
        for tool in bench_page.TOOLS:
            for k in ("found", "truth", "false_alarms", "cases"):
                assert sum(view["repos"][r][tool][k] for r in bench_page.REPOS) == view["total"][tool][k]


def test_headline_numbers_come_from_the_data(data: dict, html: str) -> None:
    hx = data["views"]["hard_x"]["total"]
    at = hx["aerotrace"]
    assert f"AeroTrace found {at['found']} of {at['truth']} with {at['false_alarms']} false alarms" in html
    for t in bench_page.TOOLS[1:]:
        assert f"{bench_page.TOOL_NAME[t]} {hx[t]['found']} with {hx[t]['false_alarms']}" in html


def test_csp_allows_only_its_own_style_and_script(html: str) -> None:
    csp = re.search(r'Content-Security-Policy" content="([^"]+)"', html).group(1)
    assert "default-src 'none'" in csp and "unsafe-inline" not in csp
    style = re.search(r"<style>(.*?)</style>", html, re.S).group(1)
    script = re.findall(r"<script>(.*?)</script>", html, re.S)
    assert len(script) == 1
    for code, kind in ((style, "style-src"), (script[0], "script-src")):
        digest = base64.b64encode(hashlib.sha256(code.encode()).digest()).decode()
        assert f"{kind} 'sha256-{digest}'" in csp


def test_loads_nothing_from_the_internet(html: str) -> None:
    assert not REMOTE.search(html)
    # The SVG namespace name is an identifier, never fetched; nothing else may look like a URL.
    rest = html.replace('"http://www.w3.org/2000/svg"', "")
    assert "http://" not in rest and "https://" not in rest


def test_not_a_mockup(html: str) -> None:
    assert "data-sample" not in html and "MOCKUP" not in html


def test_tokens_say_not_counted_until_they_are(data: dict, html: str) -> None:
    if not data["tokens_counted"]:
        assert "Tokens not counted yet." in html
        assert data["views"]["all"]["total"]["aerotrace"]["tokens"] is None


def test_wrong_safe_answers_are_named(data: dict, html: str) -> None:
    """Every build case that breaks while AeroTrace says nothing is sure to break is named on the page."""
    for cid in data["wrong_safe"]:
        raw = json.loads((RUN / "raw" / cid / "aerotrace.json").read_text())
        assert bench_page.SAFE_PHRASE in raw["text"]
        assert next(c for c in data["cases"] if c["id"] == cid)["breaks"] > 0
        assert cid in html
    if data["wrong_safe"]:
        assert 'A wrong "safe" answer' in html


def test_errored_cases_count_as_misses() -> None:
    rows = [{"truth": ["a", "b"], "tools": {"x": {"error": "boom"}}},
            {"truth": ["c"], "tools": {"x": {"found": 1, "truth_sites": 1, "false_alarms": 0, "seconds": 0.1}}}]  # fmt: skip
    agg = bench_page._agg(rows, "x")
    assert (agg["found"], agg["truth"], agg["errors"]) == (1, 3, 1)


def test_medians_match_the_scorer(data: dict) -> None:
    groups = json.loads((RUN / "results.json").read_text())["groups"]
    for view, key in bench_page.VIEW_GROUP.items():
        for tool in bench_page.TOOLS:
            assert data["views"][view]["total"][tool]["seconds"] == pytest.approx(
                groups[key][tool]["median_seconds"], abs=1e-4
            ), (view, tool)


def test_false_alarms_are_also_compared_where_aerotrace_named_a_break(data: dict, html: str) -> None:
    """A tool that names no break has no false alarm; the page must not sell that as an edge."""
    groups = json.loads((RUN / "results.json").read_text())["groups"]["all/hard_aerotrace_named_a_break"]
    named = data["named"]
    assert named["cases"] == groups["aerotrace"]["cases"]
    for t in bench_page.TOOLS:
        assert named["total"][t]["false_alarms"] == groups[t]["false_alarms"]
    if (named["total"]["aerotrace"]["found"], named["total"]["aerotrace"]["false_alarms"]) == (
        named["total"]["clangd"]["found"],
        named["total"]["clangd"]["false_alarms"],
    ):
        assert "AeroTrace did not beat clangd here." in html
    assert "edge here is few false alarms" not in html


def test_a_no_break_answer_on_a_breaking_case_is_quoted(data: dict, html: str) -> None:
    for cid, line in data["claimed_none_open"].items():
        assert cid in html and escape(line) in html
