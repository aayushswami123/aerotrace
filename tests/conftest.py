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


def _unchecked_evidence(c: dict) -> bool:
    """Any sign in the JSON that part of the change was not checked or not followed."""
    if c.get("unchecked"):
        return True
    nf = (c.get("change") or {}).get("not_followed") or {}
    if any(nf.get(k) for k in nf):
        return True
    s = c.get("summary") or {}
    if s.get("not_followed") or s.get("unknown"):
        return True
    spots = (c.get("sections") or {}).get("blind_spots") or []
    return any(b.get("unchecked", b["scope"] == "change") for b in spots)


@pytest.fixture(autouse=True)
def no_wrong_all_clear(monkeypatch):
    """Every check answer made in any test (A-04): when part of the change was not checked, the first line
    of the text and of the HTML never says "Nothing found", the JSON verdict is not "clear", and the JSON
    passes the contract check (the one `--json` prints)."""
    from aerotrace.analysis import check
    from aerotrace.ui import html, schema, terminal

    real = check.to_contract

    def wrapped(*args, **kw):
        c = real(*args, **kw)
        schema.check(c)
        if _unchecked_evidence(c):
            assert c.get("verdict") in ("breaks", "unknown"), c.get("verdict")
            first = terminal.render(c, unicode=True).splitlines()[0]
            assert "Nothing found" not in first, first
            assert "Nothing found" not in html.render(c).split("</h1>", 1)[0]
        return c

    monkeypatch.setattr(check, "to_contract", wrapped)
