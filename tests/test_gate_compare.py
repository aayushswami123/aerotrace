"""The gate's comparison logic (bench/gate/compare.py). Pure functions: no compiler, no AeroTrace."""

from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bench" / "gate"))

from compare import compare, counts, counts_from_classes, expectation_error, known_block

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


def test_expectation_must_match_the_real_build():
    assert expectation_error("c", "fails", False, ["a.c"], ["a.c", "b.c"]) == ""
    assert expectation_error("c", "passes", True, [], []) == ""
    assert expectation_error("c", "n/a", True, [], []) == ""
    msg = expectation_error("c", "passes", False, [], ["a.c"])
    assert "c" in msg and "'passes'" in msg and "fails" in msg
    assert "failing_sites" in expectation_error("c", "fails", False, ["x.c"], ["a.c"])


# -- unknown reasons and the reason table (info only) --

from compare import (  # noqa: E402
    percentile,
    reason_changes,
    reason_lines,
    reason_table,
    unknown_reason,
)


def doc(status: str = "ok", reason: str | None = None, nodes: list | None = None, **nf: list) -> dict:
    return {
        "status": status,
        "unchecked_reason": reason,
        "nodes": nodes or [],
        "change": {"not_followed": nf},
    }


def test_reason_not_ok_status_with_kind():
    assert unknown_reason(doc("nothing_to_check", mismatched=["a.c"])) == (
        "no check: nothing_to_check (the patch does not match)"
    )
    assert unknown_reason(doc("no_index")) == "no check: no_index"


def test_reason_not_followed_order_and_outside_what():
    d = doc(outside=[{"file": "a.h", "what": "an include"}], deleted=["x.c"])
    assert unknown_reason(d) == "an include outside a function"
    assert unknown_reason(doc(outside=[{"file": "a.h", "what": None}])) == "code outside any function"
    d = doc(outside=[{"file": "a.h", "what": "the declaration of secret (file-scope code is not followed)"}])
    assert unknown_reason(d) == "a declaration outside a function"
    assert unknown_reason(doc(deleted=["x.c"], build_files=["Makefile"])) == "a file was deleted or renamed"
    assert unknown_reason(doc(mismatched=["a"], not_indexed=["b"])) == "the patch does not match"
    assert unknown_reason(doc(no_text=["a"])) == "a changed file has no text in the patch"
    assert unknown_reason(doc(build_files=["Makefile"])) == "a build file changed"


def test_reason_has_no_names():
    r = unknown_reason(doc(not_indexed=["secret_file.c"]))
    assert "secret_file" not in r


def test_reason_unchecked_regexes_and_unknown_node_and_other():
    assert unknown_reason(doc(reason="foo is named in x.c (a file outside the build)")) == (
        "named in a file outside the build"
    )
    assert unknown_reason(doc(reason="2 calls of calc not compiled")) == (
        "a call not compiled with the new signature"
    )
    assert (
        unknown_reason(doc(reason="tables or pointers that name calc"))
        == "a table or pointer names the function"
    )
    assert (
        unknown_reason(doc(reason="1 call of f not compiled")) == "a call not compiled with the new signature"
    )
    assert unknown_reason(doc(reason="something new")) == "other"
    node = {"status": "unknown", "name": "f", "file": "a.c"}
    assert unknown_reason(doc(reason="f in a.c", nodes=[node])) == "an UNKNOWN item"
    assert unknown_reason(doc(reason="something new", nodes=[node])) == "other"


def test_reason_is_stable():
    d = doc(reason="the layout of S changed")
    assert unknown_reason(d) == unknown_reason(d) == "struct layout changed"


def rcase(patch: tuple[int, str, str] | None, git: tuple[int, str, str] | None = None) -> dict:
    e: dict = {"build": "fails", "expect": "fails", "sites": "", "error": ""}
    for mode, v in (("patch", patch), ("git", git)):
        if v:
            e[mode] = {"rc": v[0], "class": v[1], **({"reason": v[2]} if v[2] else {})}
    return e


RC = data(
    a=rcase((2, "honest unknown", "x"), (2, "false unknown", "x")),
    b=rcase((2, "honest unknown", "y"), (1, "correct", "")),
    c=rcase((0, "correct", "")),
)


def test_reason_table_sorted_with_counts():
    assert reason_table(RC["cases"]) == [("x", 1, 1, 2), ("y", 1, 0, 1)]


def test_reason_lines_has_table_and_percent():
    lines = reason_lines(RC["cases"])
    text = "\n".join(lines)
    assert "UNKNOWN: 3 of 5 answers (60.0%); quality bar: under 2%" in text
    assert text.index("\nx ") < text.index("\ny ")


def test_reason_lines_old_data_without_reasons():
    old = data(a=rcase((2, "honest unknown", "")))
    assert "UNKNOWN: 1 of 1 answers" in "\n".join(reason_lines(old["cases"]))


def test_reason_changes_info_only():
    cur = copy.deepcopy(RC)
    cur["cases"]["b"] = rcase((2, "honest unknown", "x"), (1, "correct", ""))
    ch = reason_changes(RC["cases"], cur["cases"])
    assert any("x" in c and "2 -> 3" in c for c in ch) and any("y" in c and "1 -> 0" in c for c in ch)
    assert compare(RC, cur, {}).ok
    assert reason_changes(BASE["cases"], RC["cases"]) == []  # baseline has no reasons: say nothing


def test_percentile_nearest_rank():
    xs = [5.0, 1.0, 3.0, 2.0, 4.0]
    assert percentile(xs, 50) == 3.0
    assert percentile(xs, 95) == 5.0
    assert percentile(xs, 20) == 1.0
    assert percentile([7.0], 95) == 7.0
    assert percentile([], 50) == 0.0
    assert percentile(list(map(float, range(1, 101))), 95) == 95.0
