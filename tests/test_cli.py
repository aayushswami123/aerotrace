import json

import pytest

from aerotrace import __version__
from aerotrace.cli import main
from conftest import GOLDEN


def test_version_flag_prints_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_doctor_finds_a_working_libclang(capsys):
    assert main(["doctor"]) == 0
    assert "libclang" in capsys.readouterr().out


def test_index_then_status(repo_factory, capsys):
    root = repo_factory("c_multi")
    assert main(["index", "--root", str(root), "--jobs", "1"]) == 0
    capsys.readouterr()
    assert main(["status", "--root", str(root), "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    golden = (GOLDEN / "c_multi.txt").read_text().splitlines()
    assert status["functions"] == sum(1 for line in golden if line.startswith("fn "))
    assert status["flags_source"] == "compile_commands.json"
    assert status["changed_since_index"] == 0


def test_status_without_index_explains_what_to_do(tmp_path, capsys):
    assert main(["status", "--root", str(tmp_path)]) == 2
    assert "aerotrace index" in capsys.readouterr().err


@pytest.fixture
def indexed(repo_factory):
    root = repo_factory("c_multi")
    assert main(["index", "--root", str(root), "--jobs", "1"]) == 0
    return root


def test_impact_prints_callers_data_and_limits(indexed, capsys):
    capsys.readouterr()
    assert main(["impact", "add", "--root", str(indexed)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Changing add (lib/shared.c:5) may affect")
    assert "get reads g_total" in out
    assert "Limits of this answer:" in out


def test_callers_json(indexed, capsys):
    capsys.readouterr()
    assert main(["callers", "add", "--root", str(indexed), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["total"] == 5
    assert data["results"][0]["confidence"] == "exact"


def test_ambiguous_name_lists_choices_and_fails(indexed, capsys):
    capsys.readouterr()
    assert main(["callees", "main", "--root", str(indexed)]) == 1
    err = capsys.readouterr().err
    assert "main@prog_a/main.c" in err and "main@prog_b/main.c" in err


def test_find_by_file_and_line(indexed, capsys):
    capsys.readouterr()
    assert main(["find", "lib/table.c:11", "--root", str(indexed)]) == 0
    assert capsys.readouterr().out.startswith("dispatch@lib/table.c")
