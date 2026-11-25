"""The dashboard's graph and node answers, and the packaged benchmark, on a real check of a fixture."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aerotrace.cli import main
from aerotrace.index import store
from aerotrace.ui import api, bench_page
from test_check import ADD_PARAM, patch
from test_check import repo as repo  # a fixture

ROOT = Path(__file__).parents[2]
RUN = ROOT / "bench" / "results" / "2026-11-24-c7df917"


@pytest.fixture
def checked(repo: Path, capsys) -> tuple[Path, str]:
    (repo / "p.diff").write_text(patch(repo, ADD_PARAM))
    main(["check", "--patch", str(repo / "p.diff"), "--root", str(repo), "--json"])
    d = store.index_dir(repo) / api.CHECKS_DIR
    d.mkdir(parents=True, exist_ok=True)
    (d / "0001-aaaaaaaa.json").write_text(capsys.readouterr().out)
    return repo, "0001-aaaaaaaa"


def test_graph_has_every_node_once_in_rings(checked: tuple[Path, str]) -> None:
    root, cid = checked
    g = api.graph(root, cid)
    saved = api.check(root, cid)
    assert sorted(n["id"] for n in g["nodes"]) == sorted(n["id"] for n in saved["nodes"])
    for n in g["nodes"]:
        assert n["ring"] >= 0 and n["group"] in (
            "changed",
            "breaks",
            "may_break",
            "affected",
            "unknown",
            "no_effect",
        )
    rings: dict[int, list[int]] = {}
    for n in g["nodes"]:
        rings.setdefault(n["ring"], []).append(n["order"])
    assert all(sorted(v) == list(range(len(v))) for v in rings.values())  # 0..n-1 in each ring
    by_id = {n["id"]: n for n in g["nodes"]}
    for n in g["nodes"]:
        if n["via"]:
            assert by_id[n["via"]]["ring"] == n["ring"] - 1  # the parent is one ring in
    assert g["counts"].get("breaks", 0) >= 1 and sum(g["counts"].values()) == len(g["nodes"])


def test_graph_is_deterministic(checked: tuple[Path, str]) -> None:
    root, cid = checked
    assert json.dumps(api.graph(root, cid), sort_keys=True) == json.dumps(
        api.graph(root, cid), sort_keys=True
    )


def test_node_details_path_and_snippet(checked: tuple[Path, str]) -> None:
    root, cid = checked
    g = api.graph(root, cid)
    brk = next(n for n in g["nodes"] if n["group"] == "breaks")
    d = api.node(root, cid, brk["id"])
    assert d["path"][-1]["id"] == brk["id"] and d["path"][0]["group"] == "changed"
    assert d["word"] in ("BREAKS", "MAY BREAK") and d["certainty_means"]
    lines = [x for x in d["snippet"]["lines"] if x["hit"]]
    assert lines and d["snippet"]["file"] == brk["file"]
    assert len(d["snippet"]["lines"]) <= api.SNIPPET_MAX


def test_node_must_be_in_the_check(checked: tuple[Path, str]) -> None:
    root, cid = checked
    with pytest.raises(api.NotFound):
        api.node(root, cid, "fn:not-in-this-check")
    with pytest.raises(api.NotFound):
        api.graph(root, "../../index")


def test_snippet_only_from_indexed_files_inside_the_repo(checked: tuple[Path, str]) -> None:
    root, _ = checked
    assert api._snippet(root, "../../etc/passwd", [1]) is None
    assert api._snippet(root, "p.diff", [1]) is None  # in the repo, but not an indexed file
    (root / "outside.c").symlink_to("/etc/hosts")
    assert api._snippet(root, "outside.c", [1]) is None


@pytest.mark.skipif(not (RUN / "results.json").exists(), reason="no benchmark run in this checkout")
def test_packaged_benchmark_is_the_published_run() -> None:
    assert api.benchmark() == json.loads(
        json.dumps(bench_page.published(bench_page.build(RUN)), sort_keys=True)
    )
