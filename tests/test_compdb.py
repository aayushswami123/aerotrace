import json

import pytest

from aerotrace.toolchain.compdb import clean_args, find_compdb, guess_units, load_compdb, unit_language


def test_clean_args_drops_compiler_output_and_source():
    args, unsafe = clean_args(
        ["gcc", "-Iinc", "-DX=1", "-c", "a.c", "-o", "a.o", "-MMD", "-MF", "a.d"], "a.c", "/r"
    )
    assert args == ["-Iinc", "-DX=1"]
    assert unsafe == 0


def test_clean_args_skips_ccache_wrapper():
    args, _ = clean_args(["ccache", "clang", "-O2", "-c", "a.c"], "a.c", "/r")
    assert args == ["-O2"]


@pytest.mark.parametrize(
    "flags",
    [
        ["-fplugin=/tmp/evil.so"],
        ["-fpass-plugin=/tmp/evil.so"],
        ["-Xclang", "-load", "-Xclang", "/tmp/evil.so"],
        ["-Xclang", "-add-plugin", "-Xclang", "evil"],
    ],
)
def test_clean_args_removes_flags_that_would_run_code(flags):
    args, unsafe = clean_args(["cc", *flags, "-Iinc", "-c", "a.c"], "a.c", "/r")
    assert args == ["-Iinc"]
    assert unsafe == 1


def test_find_compdb_prefers_repo_root_then_build_folders(tmp_path):
    assert find_compdb(tmp_path) is None
    (tmp_path / "build-debug").mkdir()
    (tmp_path / "build-debug" / "compile_commands.json").write_text("[]")
    assert find_compdb(tmp_path) == tmp_path / "build-debug" / "compile_commands.json"
    (tmp_path / "compile_commands.json").write_text("[]")
    assert find_compdb(tmp_path) == tmp_path / "compile_commands.json"


def test_find_compdb_with_missing_build_dir_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        find_compdb(tmp_path, tmp_path / "nope")


def test_load_compdb_counts_extra_configs_and_skips_outside_files(tmp_path):
    (tmp_path / "a.c").write_text("int x;")
    entries = [
        {"directory": str(tmp_path), "file": "a.c", "command": "cc -DONE -c a.c"},
        {"directory": str(tmp_path), "file": "a.c", "command": "cc -DTWO -c a.c"},
        {"directory": "/elsewhere", "file": "/elsewhere/b.c", "command": "cc -c /elsewhere/b.c"},
    ]
    (tmp_path / "compile_commands.json").write_text(json.dumps(entries))
    rep = load_compdb(tmp_path / "compile_commands.json", tmp_path.resolve(), [])
    assert [u.args for u in rep.units] == [["-DONE"]]
    assert rep.units[0].configs == 2
    assert rep.skipped_outside_repo == 1


def test_guess_parses_c_as_c_and_cpp_as_cpp(tmp_path):
    (tmp_path / "a.c").write_text("")
    (tmp_path / "b.cpp").write_text("")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "gen.c").write_text("")
    units = guess_units(tmp_path.resolve(), []).units
    assert [unit_language(u) for u in units] == ["c", "c++"]
    assert all("build" not in u.file for u in units)
