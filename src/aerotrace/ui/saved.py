"""Saved check answers, for the dashboard (`aerotrace ui`): `.aerotrace/checks/<id>.json` + `<id>.run.json`.

`<id>.json` is the same answer `aerotrace check --json` prints, with its keys sorted (deterministic: no
dates, no machine paths).
`<id>.run.json` holds what changes from run to run: the date, where the change came from, and the tool
version. The id is `NNNN-<first 8 hex of the answer's sha256>`; NNNN counts up. Only the last KEEP runs
are kept. Everything is written inside `.aerotrace/`, never anywhere else.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from aerotrace.index import store

CHECKS_DIR = "checks"
KEEP = 50
_ID = re.compile(r"^(\d{4,})-[0-9a-f]{8}$")


def checks_dir(root: Path) -> Path:
    return store.index_dir(root) / CHECKS_DIR


def source_of(base: str | None, patch: str | None) -> str:
    """Where the change came from, in words: 'working tree vs HEAD', 'patch fix.diff', ..."""
    if patch:
        return "patch from stdin" if patch == "-" else f"patch {Path(patch).name}"
    return f"working tree vs {base or 'HEAD'}"


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")  # one temp name per process: no clashes
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)  # nothing left behind when the write fails (a full disk, say)


def save(root: Path, data: dict, source: str, tool: str, now: datetime | None = None) -> Path:
    """Save one answer and return its path. Older runs beyond KEEP are deleted (only in .aerotrace/checks)."""
    d = checks_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    body = json.dumps(data, indent=2, sort_keys=True) + "\n"
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:8]
    numbers = [int(m.group(1)) for p in d.glob("*.json") if (m := _ID.match(p.stem))]
    check_id = f"{max(numbers, default=0) + 1:04d}-{digest}"
    path = d / f"{check_id}.json"
    _atomic_write(path, body)
    run = {"date": (now or datetime.now(UTC)).isoformat(timespec="minutes"), "source": source, "tool": tool}
    _atomic_write(d / f"{check_id}.run.json", json.dumps(run, indent=2, sort_keys=True) + "\n")
    _prune(d)
    return path


def _prune(d: Path) -> None:
    runs = sorted((int(m.group(1)), p) for p in d.glob("*.json") if (m := _ID.match(p.stem)) and p.is_file())
    for _, p in runs[:-KEEP] if len(runs) > KEEP else []:
        p.unlink(missing_ok=True)
        p.with_name(p.stem + ".run.json").unlink(missing_ok=True)
