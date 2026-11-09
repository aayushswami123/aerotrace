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
