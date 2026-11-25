"""The dashboard's data layer (ui/api.py): read-only, deterministic, honest about what is missing."""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from aerotrace.cli import main
from aerotrace.index import store
from aerotrace.ui import api

SAMPLE = Path(__file__).parents[1] / "fixtures" / "ui" / "check-v1-sample.json"


@pytest.fixture
def indexed(repo_factory) -> Path:
    root = repo_factory("c_multi")
    assert main(["index", "--root", str(root), "--jobs", "1"]) == 0
    return root


def _save(root: Path, name: str, data: object, run: dict | None = None) -> Path:
    d = store.index_dir(root) / api.CHECKS_DIR
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.json"
    p.write_text(data if isinstance(data, str) else json.dumps(data))
    if run is not None:
        (d / f"{name}.run.json").write_text(json.dumps(run))
    return p


def test_no_index_says_how_to_fix_it(tmp_path: Path) -> None:
    o = api.overview(tmp_path)
    assert o["status"] == "no_index"
    assert o["fix"] == "aerotrace index"
    assert o["recent_checks"] == []


def test_old_index_is_an_error_with_the_fix(indexed: Path) -> None:
    conn = sqlite3.connect(store.db_path(indexed))
    conn.execute("UPDATE meta SET value = '1' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()
    o = api.overview(indexed)
    assert o["status"] == "index_error"
    assert "older AeroTrace" in o["message"] and o["fix"] == "aerotrace index"


def test_overview_counts_come_from_the_index(indexed: Path) -> None:
    o = api.overview(indexed)
    assert o["status"] == "ok"
    conn = store.connect(indexed)
    try:
        assert o["index"]["functions"] == conn.execute("SELECT COUNT(*) FROM functions").fetchone()[0]
        assert o["index"]["files"] == o["index"]["parsed"] + o["index"]["headers"]
    finally:
        conn.close()
    cov = o["coverage"]
    assert cov["checked"] <= cov["build_files"]
    assert o["changed_since_index"] == 0
    assert o["repo"] == indexed.resolve().name


def test_overview_is_deterministic_and_has_no_dates(indexed: Path) -> None:
    a, b = api.overview(indexed), api.overview(indexed)
    assert a == b
    assert "indexed" not in json.dumps(a)  # dates live in runinfo() only


def test_changed_files_are_reported(indexed: Path) -> None:
    f = next(p for p in sorted(indexed.rglob("*.c")) if ".aerotrace" not in p.parts)
    f.write_text(f.read_text() + "\n/* edited */\n")
    o = api.overview(indexed)
    assert o["changed_since_index"] == 1


def test_saved_checks_newest_first_with_verdicts(indexed: Path) -> None:
    data = json.loads(SAMPLE.read_text())
    _save(
        indexed, "0001-aaaa1111", data, {"date": "2026-11-20T10:00+00:00", "source": "working tree vs HEAD"}
    )
    _save(indexed, "0002-bbbb2222", data)
    got = api.checks(indexed)["checks"]
    assert [c["id"] for c in got] == ["0002-bbbb2222", "0001-aaaa1111"]
    assert got[0]["ok"] and got[0]["verdict"] and got[0]["level"] == "breaks"
    assert got[0]["source"] is None and got[1]["source"] == "working tree vs HEAD"
    assert api.runinfo(indexed)["checks"] == {"0001-aaaa1111": "2026-11-20T10:00+00:00"}
    assert api.overview(indexed)["recent_checks"][0]["id"] == "0002-bbbb2222"


def test_a_broken_saved_check_is_shown_not_hidden(indexed: Path) -> None:
    _save(indexed, "0003-cccc3333", "{not json")
    _save(indexed, "0004-dddd4444", {"format": "something-else"})
    got = {c["id"]: c for c in api.checks(indexed)["checks"]}
    assert not got["0003-cccc3333"]["ok"] and "can't be read" in got["0003-cccc3333"]["problem"]
    assert not got["0004-dddd4444"]["ok"]


def test_check_ids_are_allow_listed(indexed: Path) -> None:
    _save(indexed, "0001-aaaa1111", json.loads(SAMPLE.read_text()))
    assert api.check(indexed, "0001-aaaa1111")["format"] == "aerotrace-check"
    for bad in ("../index", "..%2Findex", "/etc/passwd", "", "0001-aaaa1111.run", "x" * 200):
        with pytest.raises(api.NotFound):
            api.check(indexed, bad)


def test_reading_never_writes(indexed: Path, tmp_path: Path) -> None:
    before = {p: p.stat().st_mtime_ns for p in indexed.rglob("*") if p.is_file()}
    api.overview(indexed)
    api.checks(indexed)
    api.runinfo(indexed)
    after = {p: p.stat().st_mtime_ns for p in indexed.rglob("*") if p.is_file()}
    assert before == after


def test_overview_without_compile_db_still_answers(repo_factory) -> None:
    root = repo_factory("c_multi")
    shutil.rmtree(store.index_dir(root), ignore_errors=True)
    assert api.overview(root)["status"] == "no_index"
