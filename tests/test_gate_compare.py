"""The gate's comparison logic (bench/gate/compare.py). Pure functions: no compiler, no AeroTrace."""

from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bench" / "gate"))

from compare import compare, counts, counts_from_classes, known_block

KNOWN = {("c3", "patch"): "why"}


def case(patch: str | None, git: str | None = None, error: str = "") -> dict:
    e: dict = {"build": "fails", "expect": "fails", "sites": "", "error": error}
    for mode, cls in (("patch", patch), ("git", git)):
        if cls:
            e[mode] = {"rc": 0 if cls in ("false safe", "correct") else 1, "class": cls}
    return e


def data(**cases: dict) -> dict:
    return {"measured": "x", "cases": cases}


BASE = data(
    c1=case("correct", "correct"),
    c2=case("honest unknown", "honest unknown"),
    c3=case("false safe", "honest unknown"),
)


def run(**changes: dict):
    cur = copy.deepcopy(BASE)
    cur["cases"].update(changes)
    return compare(BASE, cur, KNOWN)


def test_same_run_passes_with_nothing_to_say():
    v = compare(BASE, copy.deepcopy(BASE), KNOWN)
    assert v.ok and not v.better and not v.changes


def test_false_unknown_counts_inside_false_alarm():
    t = counts_from_classes(["false unknown", "false alarm", "correct"])
    assert (t["false_alarm"], t["false_unknown"], t["correct"], t["cases"]) == (2, 1, 1, 3)


def test_correct_going_wrong_fails_and_names_the_case():
    v = run(c1=case("honest unknown", "correct"))
    assert not v.ok
    assert any("correct went down" in f for f in v.failures)
    assert v.changes == ["c1 (patch): correct -> honest unknown"]


def test_new_false_safe_fails_even_if_counts_are_otherwise_fine():
    v = run(c2=case("false safe", "honest unknown"), c3=case("honest unknown", "honest unknown"))
    assert not v.ok
    assert any("c2 (patch mode): false safe, and not in the known limits" in f for f in v.failures)


def test_more_weak_or_false_alarm_fails():
    assert not run(c1=case("weak", "correct")).ok
    assert not run(c1=case("false alarm", "correct")).ok
    assert not run(c1=case("false unknown", "correct")).ok


def test_improvement_passes_with_a_note():
    v = run(c2=case("correct", "honest unknown"), c3=case("honest unknown", "honest unknown"))
    assert v.ok and v.better


def test_false_unknown_alone_going_up_fails():
    base = data(c1=case("false alarm", "correct"))
    cur = data(c1=case("false unknown", "correct"))
    v = compare(base, cur, {})
    assert any("false unknown went up" in f for f in v.failures)


def test_error_missing_and_added_cases_fail():
    assert any("ERROR" in f for f in run(c1=case(None, None, error="boom")).failures)
    cur = copy.deepcopy(BASE)
    del cur["cases"]["c1"]
    assert any("missing" in f for f in compare(BASE, cur, KNOWN).failures)
    cur = copy.deepcopy(BASE)
    cur["cases"]["c9"] = case("correct", "correct")
    assert any("not in the baseline" in f for f in compare(BASE, cur, KNOWN).failures)


def test_known_limit_block_and_improvement():
    lines, better = known_block(BASE, KNOWN, "ISSUE")
    assert "ISSUE" in lines[0] and "c3 (patch mode): exit 0, false safe" in lines[1] and not better
    fixed = copy.deepcopy(BASE)
    fixed["cases"]["c3"] = case("honest unknown", "honest unknown")
    _, better = known_block(fixed, KNOWN, "ISSUE")
    assert better and "no longer false safe" in better[0]


def test_counts_per_mode_skip_cases_without_that_mode():
    d = data(a=case("correct", None), b=case("weak", "correct"))
    assert counts(d["cases"], "patch")["cases"] == 2
    assert counts(d["cases"], "git")["cases"] == 1
