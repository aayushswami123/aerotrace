"""In-process parses change the working folder; two at once must not see each other's folder (#46).

Each test parses two files at the same time, from two build folders that both use `-Iinc` (a relative
path) and include "h.h". Folder a's h.h declares from_a, folder b's declares from_b. libclang is slowed
down so the two parses overlap: without the lock, the first parse runs in the second one's folder and
reads the wrong h.h.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

from aerotrace.frontends.cpp import parse as parse_mod
from aerotrace.toolchain.compdb import CompileUnit


@pytest.fixture
def two_folders(tmp_path: Path) -> Path:
    for name in ("a", "b"):
        (tmp_path / name / "inc").mkdir(parents=True)
        (tmp_path / name / "inc" / "h.h").write_text(f"int from_{name}(int);\n")
        (tmp_path / name / "x.c").write_text('#include "h.h"\nint use(void) { return 0; }\n')
    return tmp_path


@pytest.fixture
def slow_libclang(monkeypatch: pytest.MonkeyPatch) -> None:
    real = parse_mod._get_index()

    class Slow:
        def parse(self, *args, **kwargs):
            time.sleep(0.3)  # the other thread starts its parse (and its chdir) meanwhile
            return real.parse(*args, **kwargs)

    monkeypatch.setattr(parse_mod, "_get_index", lambda: Slow())


def _both(job) -> dict[str, object]:
    out: dict[str, object] = {}
    threads = [threading.Thread(target=lambda n=n: out.__setitem__(n, job(n))) for n in ("a", "b")]
    for t in threads:
        t.start()
        time.sleep(0.05)  # a starts first, b while a is still parsing
    for t in threads:
        t.join()
    return out


def test_two_outlines_at_once_each_read_their_own_headers(two_folders: Path, slow_libclang: None) -> None:
    cwd = os.getcwd()

    def outline(name: str) -> dict | None:
        folder = two_folders / name
        src = folder / "x.c"
        return parse_mod.function_outline(str(src), src.read_text(), ["-Iinc"], str(folder))

    got = _both(outline)
    for name, other in (("a", "b"), ("b", "a")):
        res = got[name]
        assert res is not None and res["errors"] == 0, (name, res)
        assert f"from_{name}" in res["protos"], (name, res["protos"])
        assert f"from_{other}" not in res["protos"], (name, res["protos"])
    assert os.getcwd() == cwd  # the folder is put back


def test_two_index_parses_at_once_each_read_their_own_headers(two_folders: Path, slow_libclang: None) -> None:
    cwd = os.getcwd()

    def parse(name: str) -> dict:
        folder = two_folders / name
        unit = CompileUnit(file=str((folder / "x.c").resolve()), directory=str(folder), args=["-Iinc"])
        return parse_mod.parse_unit(unit, two_folders.resolve(), [], [])

    got = _both(parse)
    for name, other in (("a", "b"), ("b", "a")):
        res = got[name]
        assert res["error_count"] == 0, (name, res["errors"])
        assert f"{name}/inc/h.h" in res["deps"], (name, sorted(res["deps"]))
        assert f"{other}/inc/h.h" not in res["deps"], (name, sorted(res["deps"]))
    assert os.getcwd() == cwd
