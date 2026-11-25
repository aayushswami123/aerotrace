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
    assert "for `aerotrace ui`" in out.err  # the first save says where it went
    gitignore = (repo / ".aerotrace" / ".gitignore").read_text().splitlines()
    assert "*" in gitignore  # and git already ignores it
    answer = (d / f"{ids[0]}.json").read_text()
    assert str(repo) not in answer and str(Path.home()) not in answer  # no machine paths in the answer


def test_the_saved_answer_is_deterministic(repo: Path, capsys) -> None:
    _run(repo)
    _run(repo)
    capsys.readouterr()
    a, b = _ids(repo)
    assert a.split("-")[1] == b.split("-")[1]  # same answer, same hash
    assert a.startswith("0001-") and b.startswith("0002-")
    assert "for `aerotrace ui`" not in capsys.readouterr().err  # the note is only for the first save


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


def test_nothing_to_check_is_saved(repo: Path, capsys) -> None:
    (repo / "notes.txt").write_text("a\n")
    (repo / "docs.diff").write_text("--- a/notes.txt\n+++ b/notes.txt\n@@ -1 +1 @@\n-a\n+b\n")
    main(["check", "--patch", str(repo / "docs.diff"), "--root", str(repo), "--json"])
    assert json.loads(capsys.readouterr().out)["status"] == "nothing_to_check"
    assert len(_ids(repo)) == 1  # nothing to check: still an answer, saved


def test_an_answer_without_a_check_is_not_saved(repo: Path, monkeypatch, capsys) -> None:
    from aerotrace.analysis import check

    def no_index(*a: object, **k: object) -> tuple[dict, str | None]:
        return {
            "format": "aerotrace-check",
            "version": 1,
            "status": "no_index",
            "message": "Run `aerotrace index`.",
        }, None

    monkeypatch.setattr(check, "check_change", no_index)
    monkeypatch.setattr(check, "exit_code", lambda data: 2)
    (repo / "p.diff").write_text(patch(repo, ADD_PARAM))
    main(["check", "--patch", str(repo / "p.diff"), "--root", str(repo), "--json"])
    assert not saved.checks_dir(repo).exists()  # nothing was checked, so nothing is saved


def test_prune_leaves_other_files_alone(tmp_path: Path) -> None:
    d = saved.checks_dir(tmp_path)
    d.mkdir(parents=True)
    keep = [d / "notes.txt", d / "mine.json", d / "0001-zzzzzzzz.json"]  # not an id: z isn't hex
    for p in keep:
        p.write_text("x")
    (d / "0002-aaaaaaaa.json").mkdir()  # a folder with an id's name is skipped, not deleted
    for i in range(saved.KEEP + 2):
        saved.save(tmp_path, {"n": i}, "working tree vs HEAD", "aerotrace test")
    assert all(p.exists() for p in keep) and (d / "0002-aaaaaaaa.json").is_dir()
    assert not list(d.glob(".*.tmp"))


def test_text_mode_also_saves(repo: Path, capsys) -> None:
    (repo / "p.diff").write_text(patch(repo, ADD_PARAM))
    main(["check", "--patch", str(repo / "p.diff"), "--root", str(repo)])
    assert len(_ids(repo)) == 1
