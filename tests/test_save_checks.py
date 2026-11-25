"""`aerotrace check` keeps its answers for `aerotrace ui` in .aerotrace/checks/ (ui/saved.py)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aerotrace.cli import main
from aerotrace.ui import saved
from test_check import ADD_PARAM, patch
from test_check import repo as repo  # a fixture


def _run(repo: Path, *extra: str) -> int:
    (repo / "p.diff").write_text(patch(repo, ADD_PARAM))
    code = main(["check", "--patch", str(repo / "p.diff"), "--root", str(repo), "--json", *extra])
    return code


def _ids(repo: Path) -> list[str]:
    return sorted(p.stem for p in saved.checks_dir(repo).glob("*.json") if not p.name.endswith(".run.json"))


def test_check_saves_its_answer_and_a_run_file(repo: Path, capsys) -> None:
    _run(repo)
    out = capsys.readouterr()
    ids = _ids(repo)
    assert len(ids) == 1 and ids[0].startswith("0001-")
    d = saved.checks_dir(repo)
    assert json.loads((d / f"{ids[0]}.json").read_text()) == json.loads(out.out)  # the same answer
    run = json.loads((d / f"{ids[0]}.run.json").read_text())
    assert run["source"] == "patch p.diff" and run["tool"].startswith("aerotrace ")
    datetime.fromisoformat(run["date"])
    assert str(repo) not in (d / f"{ids[0]}.run.json").read_text()  # no machine paths
    assert ".gitignore" in out.err  # the first save says where it went and how to keep it out of git


def test_the_saved_answer_is_deterministic(repo: Path, capsys) -> None:
    _run(repo)
    _run(repo)
    capsys.readouterr()
    a, b = _ids(repo)
    assert a.split("-")[1] == b.split("-")[1]  # same answer, same hash
    assert a.startswith("0001-") and b.startswith("0002-")
    assert "gitignore" not in capsys.readouterr().err  # the hint is only for the first save


def test_no_save(repo: Path, capsys) -> None:
    _run(repo, "--no-save")
    assert not saved.checks_dir(repo).exists()


def test_only_the_last_runs_are_kept(tmp_path: Path) -> None:
    data = {"format": "aerotrace-check", "version": 1}
    when = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(saved.KEEP + 3):
        saved.save(tmp_path, {**data, "n": i}, "working tree vs HEAD", "aerotrace test", now=when)
    ids = sorted(
        p.stem for p in saved.checks_dir(tmp_path).glob("*.json") if not p.name.endswith(".run.json")
    )
    runs = list(saved.checks_dir(tmp_path).glob("*.run.json"))
    assert len(ids) == saved.KEEP and len(runs) == saved.KEEP
    assert ids[0].startswith("0004-") and ids[-1].startswith(f"{saved.KEEP + 3:04d}-")


def test_a_failed_save_does_not_fail_the_check(repo: Path, capsys) -> None:
    d = saved.checks_dir(repo)
    d.parent.mkdir(exist_ok=True)
    d.write_text("not a folder")  # so the save can't create it
    code = _run(repo)
    out = capsys.readouterr()
    assert code == 1 and json.loads(out.out)["format"] == "aerotrace-check"
    assert "could not save" in out.err


@pytest.mark.parametrize(
    ("base", "patch_name", "want"),
    [
        ("HEAD", None, "working tree vs HEAD"),
        ("main", None, "working tree vs main"),
        (None, "-", "patch from stdin"),
        ("HEAD", "/tmp/x/fix.diff", "patch fix.diff"),
    ],
)
def test_source_words(base: str | None, patch_name: str | None, want: str) -> None:
    assert saved.source_of(base, patch_name) == want
