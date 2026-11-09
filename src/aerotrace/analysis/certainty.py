"""How sure an answer is, in three words everyone can read: exact, probable, unknown.

The detailed link labels (exact, virtual, ambiguous, via_pointer, data, ...) stay in the data; this maps
them to the level an agent should act on, with the reason in plain English.
"""

from __future__ import annotations

EXACT = "exact"
PROBABLE = "probable"
UNKNOWN = "unknown"
LEVELS = (EXACT, PROBABLE, UNKNOWN)

MEANING = {
    EXACT: "the compiler resolved this exact link",
    PROBABLE: "the link is real, but may not apply here (pointer, virtual call, same name, other object)",
    UNKNOWN: "the data leaves code AeroTrace can follow; what happens next is not known",
}

_CALL_LINK = {
    "exact": (EXACT, "calls it directly"),
    "virtual": (PROBABLE, "a virtual call that may land on this override"),
    "ambiguous": (PROBABLE, "another function has the same name; this call may go to either"),
    "via_pointer": (PROBABLE, "calls a function pointer of the same type"),
}


def of_call(confidence: str) -> tuple[str, str]:
    """(level, reason) for a call link with this confidence label."""
    return _CALL_LINK.get(confidence, (PROBABLE, f"linked as '{confidence}'"))


def weakest(levels: list[str]) -> str:
    return max(levels, key=LEVELS.index) if levels else EXACT


def of_access(confidence: str, var_kind: str, question: str = "declaration") -> tuple[str, str]:
    """(level, reason) for a function that reads or writes a field or global (an edge with `confidence`).

    question "declaration" (rename, remove, retype): the code names it, so every access is exact.
    question "value" (what reads what one function writes): fields are tracked per struct type, not per
    object, so a reader of a field is only probable; a global is one object, so its reader is exact.
    """
    what = {"field": "field", "global": "global"}.get(var_kind, "static variable")
    if confidence == "ambiguous":
        return PROBABLE, f"another {what} has the same name; this may be the other one"
    if question == "value":
        if var_kind == "field":
            return PROBABLE, "reads the same field, maybe of another object"
        return EXACT, f"reads the same {what}"
    return EXACT, f"names this {what}"
