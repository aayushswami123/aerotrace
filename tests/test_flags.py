"""Compile flags on messy real-world builds: cross compilers, GCC-only flags, response files, several
databases, files the build leaves out, missing headers, and a database that changes."""

import json
import shutil
from pathlib import Path

import pytest

from aerotrace.analysis import graph
from aerotrace.cli import main
from aerotrace.frontends.cpp.parse import parse_unit
from aerotrace.index import store
from aerotrace.index.build import build_index, fresh_index
from aerotrace.toolchain.compdb import (
    CompileUnit,
    build_system_hint,
    expand_response_files,
    find_units,
    load_compdb,
    target_from_compiler,
)
from conftest import FIXTURES

M4F = ["-mcpu=cortex-m4", "-mthumb", "-mfloat-abi=hard", "-mfpu=fpv4-sp-d16"]


@pytest.mark.parametrize(
    "compiler, triple",
    [
        (["arm-none-eabi-gcc"], "arm-none-eabi"),
        (["/opt/gcc-arm/bin/arm-none-eabi-gcc"], "arm-none-eabi"),
        (["ccache", "aarch64-linux-gnu-g++-12"], "aarch64-linux-gnu"),
        (["x86_64-w64-mingw32-gcc.exe"], "x86_64-w64-mingw32"),
        (["riscv64-unknown-elf-clang"], "riscv64-unknown-elf"),
        (["gcc"], None),
        (["clang++"], None),
        (["cc"], None),
        (["/usr/bin/gcc-12"], None),
    ],
)
def test_target_comes_from_the_compiler_name(compiler, triple):
    assert target_from_compiler([*compiler, "-c", "a.c"]) == triple


def cross_repo(tmp_path: Path, compiler: str = "arm-none-eabi-gcc", extra: list[str] | None = None) -> Path:
    """The c_cross fixture with a compile database in build/, as an embedded build would write it.
    Flags for board.c come through a response file."""
    root = tmp_path / "c_cross"
    shutil.copytree(FIXTURES / "c_cross", root)
    build = root / "build"
    build.mkdir()
    (build / "flags.rsp").write_text(" ".join([*M4F, "-I../include", *(extra or [])]))
    entries = [
        {
            "directory": str(build),
            "file": "../src/main.c",
            "arguments": [
                compiler,
                *M4F,
                "-I../include",
                *(extra or []),
                "-c",
                "../src/main.c",
                "-o",
                "main.o",
            ],
        },
        {
            "directory": str(build),
            "file": "../src/board.c",
            "arguments": [compiler, "@flags.rsp", "-c", "../src/board.c", "-o", "board.o"],
        },
    ]
    (build / "compile_commands.json").write_text(json.dumps(entries))
    return root


def callees(root: Path, name: str) -> set[str]:
    conn = store.connect(root)
    try:
        [fn] = [f for f in graph.symbols.find(conn, name) if f.file.startswith("src/")]
        return {h.fn.qualified_name for h in graph.walk(conn, fn, "callees", 1).hits}
    finally:
        conn.close()


def test_cross_build_parses_for_its_target(tmp_path):
    root = cross_repo(tmp_path)
    report = build_index(root, jobs=1)
    assert report.targets == ["arm-none-eabi"]
    assert report.files_with_errors == 0 and report.machine_flags_dropped == 0
    # the Cortex-M4F-only branch is seen, because we parse for the real target
    assert callees(root, "main") == {"fpu_init", "led_on"}


def test_response_files_are_read_and_their_flags_cleaned(tmp_path):
    root = cross_repo(tmp_path, extra=["-fplugin=/tmp/evil.so"])
    flags = load_compdb(root / "build" / "compile_commands.json", root, [])
    board = next(u for u in flags.units if u.file.endswith("board.c"))
    assert "-I../include" in board.args and "-mcpu=cortex-m4" in board.args
    assert flags.response_files == 1
    assert flags.unsafe_flags_removed == 2  # one on the command line, one inside the response file


def test_response_files_outside_the_repo_and_build_dir_are_not_read(tmp_path):
    secret = tmp_path / "elsewhere.rsp"
    secret.write_text("-DSECRET")
    (tmp_path / "repo").mkdir()
    args, read, skipped = expand_response_files(
        ["-O2", f"@{secret}", "@missing.rsp"], str(tmp_path / "repo"), [tmp_path / "repo"]
    )
    assert args == ["-O2"] and read == 0
    assert skipped == [str(secret), "missing.rsp"]


def test_gcc_only_flags_are_not_parse_errors(tmp_path):
    src = tmp_path / "a.c"
    src.write_text("int f(void) { return 1; }\n")
    unit = CompileUnit(
        str(src), str(tmp_path), ["-x", "c", "-fno-tree-loop-distribute-patterns", "-fstack-usage"]
    )
    res = parse_unit(unit, tmp_path, [], [])
    assert res["error_count"] == 0
    assert "-fno-tree-loop-distribute-patterns" in res["ignored_flags"]
    assert [f["name"] for f in res["functions"]] == ["f"]


def test_target_flags_with_an_unknown_compiler_still_parse(tmp_path):
    # blind spot: with a compiler we can't read a target from, the -m flags are dropped and that is reported
    root = cross_repo(tmp_path, compiler="xc32-gcc")
    report = build_index(root, jobs=1)
    assert report.targets == []
    assert report.files_with_errors == 0
    assert "led_on" in callees(root, "main")
    if report.machine_flags_dropped:  # the host clang refused -mfloat-abi=hard and friends
        conn = store.connect(root)
        assert any("-m target flags" in n for n in graph.index_limits(conn))
        conn.close()


def test_sources_the_build_leaves_out_are_reported(tmp_path):
    root = cross_repo(tmp_path)
    build_index(root, jobs=1)
    conn = store.connect(root)
    notes = graph.index_limits(conn)
    conn.close()
    assert any(
        "1 C/C++ source file is not in compile_commands.json" in n and "legacy/old_driver.c" in n
        for n in notes
    )


def test_missing_headers_are_summarised(tmp_path):
    root = cross_repo(tmp_path)
    (root / "src" / "main.c").write_text(
        '#include "board.h"\n#include "stm32f4xx_hal.h"\nint main(void) { led_on(); }\n'
    )
    report = build_index(root, jobs=1)
    assert report.missing_headers == {"stm32f4xx_hal.h": 1}
    conn = store.connect(root)
    assert any("stm32f4xx_hal.h (1 file)" in n for n in graph.index_limits(conn))
    conn.close()


def test_other_databases_are_reported_not_silently_ignored(tmp_path):
    root = cross_repo(tmp_path)
    shutil.copytree(root / "build", root / "build-host")
    flags = find_units(root, None, [])
    assert flags.path == "build/compile_commands.json"
    assert flags.alternatives == ["build-host/compile_commands.json"]


def test_refresh_keeps_the_build_dir_and_excludes(tmp_path, repo_factory):
    root = repo_factory("c_multi", compdb=False)
    build = tmp_path / "out-of-tree"
    build.mkdir()
    entries = [
        {"directory": str(root), "file": f, "arguments": ["cc", "-Iinclude", "-c", f]}
        for f in ("lib/shared.c", "prog_a/main.c", "prog_a/util.c")
    ]
    (build / "compile_commands.json").write_text(json.dumps(entries))
    build_index(root, build_dir=build, excludes=["prog_a/util.c"], jobs=1)
    (root / "lib" / "shared.c").write_text((root / "lib" / "shared.c").read_text() + "\n")
    conn, note = fresh_index(root)  # what the MCP server does, without --build-dir
    meta = store.read_meta(conn)
    conn.close()
    assert note.startswith("(Re-indexed 1 changed file(s)")
    assert meta["flags_source"] == "compile_commands.json" and meta["build_dir"] == str(build)
    assert json.loads(meta["excludes"]) == ["prog_a/util.c"]


def test_a_changed_database_triggers_a_reindex(tmp_path):
    root = cross_repo(tmp_path)
    build_index(root, jobs=1)
    conn, note = fresh_index(root)
    conn.close()
    assert note == ""
    db = root / "build" / "compile_commands.json"
    entries = json.loads(db.read_text())
    entries[0]["arguments"].insert(1, "-DBOARD_REV=2")
    db.write_text(json.dumps(entries))
    conn, note = fresh_index(root)
    conn.close()
    assert note == "(Re-indexed: compile_commands.json changed.)"


@pytest.mark.parametrize(
    "files, word",
    [(["CMakeLists.txt"], "CMake"), (["meson.build"], "Meson"), (["Makefile"], "bear"), ([], "bear")],
)
def test_build_system_hint(tmp_path, files, word):
    for f in files:
        (tmp_path / f).write_text("")
    assert word in build_system_hint(tmp_path)


def test_doctor_reports_target_and_left_out_files(tmp_path, capsys):
    root = cross_repo(tmp_path)
    assert main(["doctor", "--root", str(root)]) == 0
    out = capsys.readouterr().out
    assert "target    arm-none-eabi" in out
    assert "1 source files are not in it: legacy/old_driver.c" in out
