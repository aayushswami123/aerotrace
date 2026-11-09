"""Shared helpers: copy a fixture to a temp folder and give it a compile_commands.json."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = Path(__file__).parent / "golden"


def make_repo(
    tmp_path: Path, fixture: str, *, compdb: bool = True, extra_args: list[str] | None = None
) -> Path:
    """Copy tests/fixtures/<fixture> to tmp and write a compile_commands.json for its sources."""
    root = tmp_path / fixture
    shutil.copytree(FIXTURES / fixture, root)
    if compdb:
        entries = []
        for src in sorted(root.rglob("*")):
            if src.suffix not in (".c", ".cpp"):
                continue
            rel = src.relative_to(root).as_posix()
            cc = ["cc", "-std=gnu11"] if src.suffix == ".c" else ["c++", "-std=c++17"]
            args = [*cc, "-Iinclude", *(extra_args or []), "-c", rel, "-o", rel + ".o"]
            entries.append({"directory": str(root), "file": rel, "arguments": args})
        (root / "compile_commands.json").write_text(json.dumps(entries, indent=2))
    return root


@pytest.fixture
def repo_factory(tmp_path):
    def factory(fixture: str, **kw) -> Path:
        return make_repo(tmp_path, fixture, **kw)

    return factory
