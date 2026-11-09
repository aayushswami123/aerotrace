"""Terminal text for `aerotrace check`: golden outputs, mode picking, and no colour-only meaning."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest
import samples

from aerotrace.ui import schema, terminal

GOLDEN = Path(__file__).parents[1] / "golden" / "ui"
FIXTURES = Path(__file__).parents[1] / "fixtures" / "ui"

CASES = {
    "sample": samples.sample,
    "nothing": samples.nothing,
    "unclear": samples.unclear,
    "affected_only": samples.affected_only,
    "no_index": samples.no_index,
    "index_error": samples.index_error,
    "nothing_to_check": samples.nothing_to_check,
    "nothing_to_check_gaps": samples.nothing_to_check_gaps,
}


def _golden(name: str, text: str) -> None:
    path = GOLDEN / name
    if os.environ.get("UPDATE_GOLDEN"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
    assert text + "\n" == path.read_text(encoding="utf-8"), (
        f"{name} changed; rerun with UPDATE_GOLDEN=1 to accept"
    )


@pytest.mark.parametrize("case", sorted(CASES))
def test_golden_unicode(case: str) -> None:
    _golden(f"{case}.txt", terminal.render(CASES[case](), unicode=True))


@pytest.mark.parametrize("case", sorted(CASES))
def test_golden_ascii(case: str) -> None:
    text = terminal.render(CASES[case](), unicode=False)
    _golden(f"{case}.ascii.txt", text)
    assert text.isascii(), "non-UTF-8 output must be plain ASCII"


def test_golden_ansi() -> None:
    _golden("sample.ansi.txt", terminal.render(samples.sample(), color=True, unicode=True))


def test_contract_file_matches_sample() -> None:
    """tests/fixtures/ui/check-v1-sample.json is the contract file Part C tests against."""
    path = FIXTURES / "check-v1-sample.json"
    text = json.dumps(samples.sample(), indent=2, sort_keys=True) + "\n"
    if os.environ.get("UPDATE_GOLDEN"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8")) == samples.sample()
    schema.check(json.loads(path.read_text(encoding="utf-8")))


@pytest.mark.parametrize("case", sorted(CASES))
def test_colour_never_carries_meaning(case: str) -> None:
    coloured = terminal.render(CASES[case](), color=True, unicode=True)
    plain = terminal.render(CASES[case](), color=False, unicode=True)
    assert terminal.strip_ansi(coloured) == plain


def test_every_item_has_a_status_word() -> None:
    text = terminal.render(samples.sample(), unicode=False, limit=100)
    words = ("BREAKS", "MAY BREAK", "AFFECTED", "UNKNOWN", "NO EFFECT FOUND", "NO TEST")
    items = [line for line in text.splitlines() if line.startswith("  [") and "] " in line[:6]]
    node_items = [line for line in items if line[2:5] in ("[x]", "[~]", "[-]", "[ ]") or "UNKNOWN" in line]
    assert node_items
    assert all(any(w in line for w in words) for line in node_items)


def test_only_exact_breaks_count_in_the_headline() -> None:
    d = samples.sample()
    first = terminal.render(d).splitlines()[0]
    assert "can break 3 things" in first  # 3 exact; the probable one is on the next line
    assert "1 more may not build or link" in terminal.render(d).splitlines()[1]
    d["summary"]["breaks_exact"] = 0
    for n in d["nodes"]:
        if n["status"] == "breaks":
            n["certainty"] = "probable"
    d["summary"]["breaks_probable"] = 4
    assert "4 things you didn't touch may not build or link." in terminal.render(d).splitlines()[0]


def test_direct_counts_come_first() -> None:
    line = terminal.render(samples.affected_only()).splitlines()[0]
    assert "(1 direct)" in line


def test_never_an_all_clear_when_part_of_the_change_was_skipped() -> None:
    text = terminal.render(samples.unclear(), unicode=True)
    assert "✅" not in text
    assert text.startswith(
        terminal.ICONS["info"][0] + " Nothing found that this change can break, but 2 parts"
    )
    ascii_text = terminal.render(samples.unclear(), unicode=False)
    assert ascii_text.startswith("[i] ")


def test_all_clear_only_when_nothing_was_skipped() -> None:
    assert terminal.render(samples.nothing()).startswith("✅ Nothing found")
    assert "Index limit: " in terminal.render(samples.nothing())  # still says what it can't see


def test_no_index_never_looks_like_a_pass() -> None:
    for case in (samples.no_index, samples.index_error):
        for uni in (True, False):
            text = terminal.render(case(), unicode=uni)
            assert "checked nothing" in text
            assert "✅" not in text and "[ok]" not in text


def test_limit_says_what_was_left_out() -> None:
    text = terminal.render(samples.sample(), limit=2)
    assert "... and 9 more (" in text


def test_control_characters_are_not_sent_to_the_terminal() -> None:
    d = samples.sample()
    d["nodes"][3]["name"] = "evil\x1b[2J\x07name"
    text = terminal.render(d, color=False)
    assert "\x1b" not in text and "\x07" not in text
    assert "evil\\u001b[2J\\u0007name" in text


def test_bidi_controls_are_shown_not_applied() -> None:
    d = samples.sample()
    d["nodes"][3]["name"] = "admin\u202eolleh\u2066x\u2028y"
    text = terminal.render(d)
    assert "admin\\u202eolleh\\u2066x\\u2028y" in text
    assert not any(c in text for c in "\u202e\u2066\u2028")


def test_ascii_mode_escapes_non_ascii_names() -> None:
    d = samples.hostile()
    text = terminal.render(d, unicode=False, limit=100)
    assert text.isascii()
    assert "na\\xefve::\\u65e5" in text
    text.encode("ascii")  # never a UnicodeEncodeError on a non-UTF-8 stream


def test_what_changed_is_named() -> None:
    text = terminal.render(samples.sample())
    block = text.split("What changed (3)", 1)[1].split("\n\n", 1)[0]
    assert "CHANGED" in block and "getGenericCommand" in block
    assert "REMOVED" in block and "checkStringLength" in block


def test_nothing_to_check_lists_what_it_skipped() -> None:
    text = terminal.render(samples.nothing_to_check_gaps())
    assert "✅" not in text
    assert "could not be followed" in text.splitlines()[0]
    assert "src/extra.c is not in the index" in text
    assert "src/t_string.c: the patch does not match" in text


def test_index_limits_sit_right_under_a_pass() -> None:
    lines = terminal.render(samples.nothing()).splitlines()
    assert lines[0].startswith("✅")
    assert "    Index limit: Macro uses are not followed anywhere in the index." in lines[:5]
    assert "Limits of the index" not in "\n".join(lines)  # not printed twice


class _Stream(io.StringIO):
    def __init__(self, tty: bool, encoding: str):
        super().__init__()
        self._tty, self._enc = tty, encoding

    def isatty(self) -> bool:
        return self._tty

    @property
    def encoding(self) -> str:  # type: ignore[override]
        return self._enc


@pytest.mark.parametrize(
    ("tty", "enc", "env", "want"),
    [
        (True, "utf-8", {}, (True, True)),
        (True, "UTF8", {"TERM": "xterm-256color"}, (True, True)),
        (False, "utf-8", {}, (False, True)),
        (True, "utf-8", {"NO_COLOR": "1"}, (False, True)),  # NO_COLOR is about colour only
        (True, "latin-1", {"NO_COLOR": "1"}, (False, False)),
        (True, "utf-8", {"NO_COLOR": ""}, (True, True)),  # no-color.org: empty does not count
        (True, "latin-1", {}, (True, False)),
        (False, "ascii", {}, (False, False)),
        (True, "utf-8", {"TERM": "dumb"}, (False, False)),
        (True, "no-such-codec", {}, (True, False)),
    ],
)
def test_style_for(tty: bool, enc: str, env: dict, want: tuple[bool, bool]) -> None:
    assert terminal.style_for(_Stream(tty, enc), env) == want
