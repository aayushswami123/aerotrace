"""The overall answer of `aerotrace check`, decided in one place (audit A-04).

`verdict` is one of, in this order:

- `breaks`: at least one BREAKS or MAY BREAK.
- `unknown`: no break found, but a part of the change was not checked or not followed.
- `clear`: every part of the change was checked and nothing breaks (things may still be AFFECTED).

`unchecked` is true whenever a part was not checked or not followed, also when the verdict is `breaks`
("1 BREAKS; 2 parts could not be checked"). The JSON of `check` carries both (`verdict`, `unchecked`,
`unchecked_parts`, `unchecked_reason`); `of()` reads them back for the CLI and the hook.

What counts as unchecked is listed in `analysis/check.py` (`unchecked_parts`). Standing limits of the
whole index (one build configuration, pointer calls matched by type, ...) do not count: they are true of
every answer, so counting them would make every answer UNKNOWN.
"""

from __future__ import annotations

from typing import NamedTuple

VERDICTS = ("breaks", "unknown", "clear")


class Verdict(NamedTuple):
    verdict: str  # breaks | unknown | clear
    unchecked: bool  # a part of the change was not checked or not followed
    parts: int  # how many parts (0 when nothing was checked at all: no index)
    reason: str | None  # the first of them, in a few words ("a macro in defs.h")


def decide(breaks: int, unchecked: bool) -> str:
    """The one rule: a break wins, then anything not checked, else clear."""
    if breaks:
        return "breaks"
    return "unknown" if unchecked else "clear"


def of(contract: dict) -> Verdict:
    """The verdict of an `aerotrace-check` JSON (any status)."""
    return Verdict(
        contract["verdict"],
        contract["unchecked"],
        contract.get("unchecked_parts", 0),
        contract.get("unchecked_reason"),
    )
