"""The aerotrace-check contract: what the renderers refuse, so a bad producer can't show a wrong all-clear."""

from __future__ import annotations

import pytest
import samples

from aerotrace.ui import html, schema, terminal


def _bad(d: dict, match: str) -> None:
    with pytest.raises(schema.ContractError, match=match):
        schema.check(d)
    with pytest.raises(schema.ContractError):
        terminal.render(d)
    with pytest.raises(schema.ContractError):
        html.render(d)


def test_samples_pass() -> None:
    for f in (samples.sample, samples.nothing, samples.unclear, samples.affected_only, samples.no_index,
              samples.index_error, samples.nothing_to_check, samples.nothing_to_check_gaps, samples.hostile):  # fmt: skip
        schema.check(f())
    schema.check(samples.big(300))


def test_wrong_format_and_versions() -> None:
    d = samples.sample()
    d["format"] = "something-else"
    _bad(d, "format")
    d = samples.sample()
    d["version"] = 2
    _bad(d, "newer AeroTrace")
    d["version"] = 0
    _bad(d, "not supported")


def test_break_can_not_be_unknown() -> None:
    d = samples.sample()
    n = next(n for n in d["nodes"] if n["status"] == "breaks")
    n["certainty"] = "unknown"
    _bad(d, "never unknown")


def test_unknown_status_and_certainty_go_together() -> None:
    d = samples.sample()
    n = next(n for n in d["nodes"] if n["status"] == "affected")
    n["certainty"] = "unknown"
    _bad(d, "go together")


def test_ids_must_exist() -> None:
    d = samples.sample()
    d["sections"]["functions"].append("fn:missing")
    _bad(d, "not a node")
    d = samples.sample()
    d["edges"].append(
        {"from": "fn:missing", "to": d["nodes"][0]["id"], "kind": "calls", "certainty": "exact"}
    )
    _bad(d, "drop edges")
    d = samples.sample()
    d["nodes"][5]["via"] = "fn:missing"
    _bad(d, "drop via")
    d = samples.sample()
    d["nodes"].append(dict(d["nodes"][0]))
    _bad(d, "twice")


def test_changed_nodes_sit_at_distance_zero() -> None:
    d = samples.sample()
    d["nodes"][0]["distance"] = 1
    _bad(d, "distance 0")


def test_summary_can_not_be_more_hopeful_than_the_nodes() -> None:
    d = samples.sample()
    d["summary"]["breaks_exact"] = 0
    _bad(d, "summary says 0 breaks exact")
    d = samples.sample()
    d["summary"].update(affected_direct=0, affected_further=0)
    _bad(d, "affected")


def test_summary_must_match_when_nothing_was_left_out() -> None:
    d = samples.sample()
    d["summary"]["breaks_exact"] = 5  # says "can break 5", but only 3 exact breaks are listed
    _bad(d, "none were left out")
    d["omitted"]["nodes"] = 2
    schema.check(d)  # fine: 2 more are not in the report


def test_no_all_clear_when_nodes_were_left_out_or_the_walk_stopped() -> None:
    d = samples.nothing()
    d["omitted"]["nodes"] = 3
    _bad(d, "left out")
    d = samples.nothing()
    d["proof"]["stopped_early"] = True
    _bad(d, "stopped early")


def test_every_node_is_in_exactly_one_section() -> None:
    d = samples.sample()
    d["sections"]["functions"].pop()
    _bad(d, "in no section")
    d = samples.sample()
    d["sections"]["data"].append(d["sections"]["functions"][0])
    _bad(d, "listed twice")
    d = samples.sample()
    d["sections"]["functions"].append(d["nodes"][0]["id"])
    _bad(d, "part of the change")


def test_non_ok_status_is_still_checked() -> None:
    d = samples.nothing_to_check()
    d["change"]["not_followed"]["outside"] = [{"file": "a.c"}]
    _bad(d, "outside")
    d = samples.no_index()
    d["nodes"] = samples.sample()["nodes"]
    _bad(d, "can't list nodes")
    d = samples.nothing_to_check()
    d["change"] = None
    _bad(d, "change is required")


def test_skipped_parts_need_a_count() -> None:
    d = samples.unclear()
    d["summary"]["not_followed"] = 0
    _bad(d, "wrong all-clear")
    d = samples.nothing()
    d["sections"]["blind_spots"].append({"text": "x", "scope": "change", "node": None})
    _bad(d, "wrong all-clear")


def test_blind_spot_scope_is_required() -> None:
    d = samples.nothing()
    d["sections"]["blind_spots"][0]["scope"] = "global"
    _bad(d, "scope")


def test_no_index_needs_a_message() -> None:
    d = samples.no_index()
    d["message"] = None
    _bad(d, "needs a message")


def test_tests_sections_may_be_null() -> None:
    d = samples.sample()
    d["sections"]["tests_to_run"] = None
    d["sections"]["untested_changes"] = None
    schema.check(d)
    assert "Tests: not known yet" in terminal.render(d)


def test_node_words() -> None:
    assert schema.node_word("breaks", "exact") == "BREAKS"
    assert schema.node_word("breaks", "probable") == "MAY BREAK"
    assert schema.node_word("no_effect", "exact") == "NO EFFECT FOUND"


def test_the_verdict_can_not_be_more_hopeful_than_the_counts() -> None:
    """A-04: `verdict` is breaks, unknown or clear; `unchecked` is true when a part was not checked."""
    d = samples.unclear()
    d["verdict"] = "clear"
    _bad(d, "wrong all-clear")
    d = samples.unclear()
    d["unchecked"] = False
    _bad(d, "unchecked")
    d = samples.nothing_to_check_gaps()
    d["verdict"] = "clear"
    _bad(d, "wrong all-clear")
    d = samples.sample()
    d["verdict"] = "unknown"
    _bad(d, "verdict")
    d = samples.nothing()
    del d["verdict"]
    _bad(d, "verdict")
    for f in (samples.sample, samples.unclear, samples.nothing_to_check_gaps, samples.no_index):
        assert f()["unchecked"] is True
    assert samples.nothing()["verdict"] == "clear" and samples.nothing()["unchecked"] is False
