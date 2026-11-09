"""Ranked, capped signature answers (work item "ranking") on tests/fixtures/c_rank, written to trip it up:

- f is defined in src/a_first.c and again in dup/f.c, so calls from files that only see the header are
  ambiguous (probable); calls in a_first.c are exact.
- z_last.c (sorts last) has the only call that does not compile.
- many() has 20 ignored calls, so the default cap of 15 call sites is reached.
- t1/t2 are tables of f; dispatch1 calls through t1 once, dispatch2 through t2 on two lines.
- top() reaches f through direct_caller() (exact) and dispatch1() (pointer); top2() and deep() only through
  dispatch1() and dispatch2() at the same distance; calls_both() through both_ways(), which calls f directly
  and through a pointer; only_ptr1..6 only through dispatch2().
"""

import json
import re

import pytest

from aerotrace.analysis import render, signature, symbols
from aerotrace.cli import main
from aerotrace.index import store
from aerotrace.index.build import build_index

F = "f@src/a_first.c"


@pytest.fixture
def rank(repo_factory):
    root = repo_factory("c_rank")
    build_index(root, jobs=1)
    conn = store.connect(root)
    yield root, conn
    conn.close()


def impact(conn, query=F, depth=2):
    [fn] = symbols.find(conn, query)
    return signature.signature_impact(conn, fn, depth)


def site_rows(text: str, header: str) -> list[str]:
    """The '  - ' rows right after `header`, up to the next line that is not a row."""
    lines = text.splitlines()
    i = next(n for n, line in enumerate(lines) if line.startswith(header))
    out = []
    for line in lines[i + 1 :]:
        if not line.startswith("  - "):
            break
        out.append(line)
    return out


def test_rank_order_broken_then_uses_then_ignores(rank):
    _, conn = rank
    r = impact(conn)
    got = [(s.caller.name, s.line, r.rank_group(s), s.certainty) for s in r.ranked(r.sites)]
    assert got[:7] == [
        ("broken_call", 4, "does_not_compile", "probable"),  # last file, but first
        ("stored_exact", 15, "uses_result", "exact"),
        ("direct_caller", 19, "uses_result", "exact"),
        ("both_ways", 22, "uses_result", "exact"),
        ("used_probable", 5, "uses_result", "probable"),  # a use beats an exact ignore
        ("ignored_first", 12, "ignores_result", "exact"),  # first file, but after every use
        ("discards", 26, "ignores_result", "exact"),  # (void) counts as ignoring
    ]
    assert got[-1] == ("ignored_probable", 9, "ignores_result", "probable")  # exact before probable
    assert len(got) == 28


def test_text_shows_ranked_sites_capped_and_counts_what_is_left(rank):
    _, conn = rank
    text = render.signature_text(impact(conn))
    rows = site_rows(text, "Call sites (28)")
    assert len(rows) == 16  # the call that does not compile, then 15 more
    assert rows[0].startswith("  - broken_call  src/z_last.c:4 [probable]") and "DOES NOT COMPILE" in rows[0]
    assert rows[4].startswith("  - used_probable  src/m_use.c:5 [probable]")
    assert "  ... and 12 more call sites (use --all to see every one, or --limit N)." in text
    assert (
        "Summary: 28 call sites in 3 files (25 exact, 3 probable, 0 unknown); 1 does not compile now; "
        in text
    )
    assert (
        "4 pointer calls (probable); 11 callers further up, 9 of them reached through a pointer call on the way."
        in text
    )
    assert "Call sites per file, most first: src/a_first.c 25, src/m_use.c 2, src/z_last.c 1." in text
    # direct and pointer uses are counted apart
    assert (
        "How callers use the return value: 5 use it (2 stored, 1 compared, 2 returned), 23 ignore it" in text
    )
    assert "How pointer calls use it: 4 use it (1 stored, 1 returned, 2 used in an expression)." in text


def test_calls_that_do_not_compile_are_never_cut(rank):
    _, conn = rank
    text = render.signature_text(impact(conn), limit=1)
    rows = site_rows(text, "Call sites (28)")
    assert [r.split()[1] for r in rows] == ["broken_call", "stored_exact"]
    assert "  ... and 26 more call sites" in text
    out = render.signature_json(impact(conn), limit=1)
    assert [s["caller"]["name"] for s in out["not_compiling_call_sites"]] == ["broken_call"]
    assert out["summary"]["not_compiling"] == 1


def test_upstream_groups_by_entry(rank):
    _, conn = rank
    r = impact(conn)
    groups = [
        (g.kind, g.entry.name if g.entry else None, g.lines, [(h.fn.name, h.confidence) for h in g.members])
        for g in r.groups
    ]
    assert groups == [
        # top: exact through direct_caller, also through dispatch1 -> direct group, once, exact
        # calls_both: through both_ways, which calls f directly and through a pointer -> exact
        # run_thunk comes in through direct_caller, but calls it through a pointer: direct group, probable
        ("direct", None, [], [("top", "exact"), ("calls_both", "exact"), ("run_thunk", "via_pointer")]),
        # top2 ties between dispatch1 and dispatch2 at the same distance: the lowest entry id wins
        ("pointer", "dispatch1", [4], [("top2", "via_pointer"), ("deep", "via_pointer")]),
        ("pointer", "dispatch2", [7, 8], [(f"only_ptr{i}", "via_pointer") for i in range(1, 7)]),
    ]
    also = {h.fn.name: g.also.get(h.fn.id, 0) for g in r.groups for h in g.members}
    assert also == {"top": 1, "calls_both": 0, "run_thunk": 0, "top2": 1, "deep": 1} | {
        f"only_ptr{i}": 0 for i in range(1, 7)
    }
    # every upstream caller is in exactly one group
    assert sorted(h.fn.id for g in r.groups for h in g.members) == sorted(h.fn.id for h in r.upstream)


def test_pointer_only_callers_are_never_exact(rank):
    _, conn = rank
    r = impact(conn)
    for g in r.groups:
        if g.kind == "pointer":
            assert all(h.confidence == "via_pointer" for h in g.members)


def test_upstream_text_headers_caps_and_also(rank):
    _, conn = rank
    text = render.signature_text(impact(conn))
    assert "  3 callers, reached through its direct call sites; 1 of them probable:" in text
    assert "    - top  src/p_ptr.c:12 via direct_caller (also reached through 1 other caller of f)" in text
    assert "  2 callers, reached through the pointer call in dispatch1() at src/p_ptr.c:4 [probable]:" in text
    assert (
        "  6 callers, reached through the pointer call in dispatch2() at src/p_ptr.c:7, 8 [probable]:" in text
    )
    assert "only_ptr5" in text and "only_ptr6" not in text  # 5 per pointer group
    assert "    ... and 1 more in this group (use --all to see every one, or --limit N)." in text
    assert "[via_pointer] = " in text and "[probable] = " in text


def test_group_headers_are_capped_but_counted(rank, monkeypatch):
    _, conn = rank
    monkeypatch.setattr(render, "GROUP_HEADER_CAP", 1)
    text = render.signature_text(impact(conn))
    assert "reached through its direct call sites" in text and "dispatch1()" not in text.split("Callers")[1]
    assert "  ... and 2 more groups (8 callers) (use --all to see every one, or --limit N)." in text


def test_every_more_count_is_total_minus_shown(rank):
    _, conn = rank
    r = impact(conn)
    for kw in ({}, {"limit": 1}, {"limit": 2}):
        text = render.signature_text(r, **kw)
        rows = site_rows(text, "Call sites (28)")
        m = re.search(r"\.\.\. and (\d+) more call sites?", text)
        assert len(rows) + int(m.group(1)) == 28
        for g in r.groups:
            shown = sum(1 for h in g.members if f"    - {h.fn.qualified_name}  {h.fn.where}" in text)
            left = len(g.members) - shown
            if left:
                assert f"... and {left} more in this group" in text
        p = site_rows(text, "Calls through a pointer")
        m = re.search(r"\.\.\. and (\d+) more pointer calls?", text)
        assert len(p) + (int(m.group(1)) if m else 0) == 4
        a = [
            line
            for line in text.splitlines()
            if line.startswith("  - src/a_first.c:") and "initializer" in line
        ]
        m = re.search(r"\.\.\. and (\d+) more places?", text)
        assert len(a) + (int(m.group(1)) if m else 0) == 2


def test_show_all_has_no_caps(rank):
    _, conn = rank
    text = render.signature_text(impact(conn), show_all=True)
    assert len(site_rows(text, "Call sites (28)")) == 28
    assert "more" not in text.replace("more first", "").replace("more step", "")
    assert "only_ptr6" in text


def test_void_function_rank_group_is_no_result(rank):
    _, conn = rank
    r = impact(conn, "note")
    assert [r.rank_group(s) for s in r.sites] == ["no_result", "no_result"]
    text = render.signature_text(r)
    assert "It returns void" in text and "ignore" not in text.split("Limits of this answer")[0]
    assert [s["rank_group"] for s in render.signature_json(r)["call_sites"]] == ["no_result", "no_result"]


def test_same_answer_twice(rank):
    _, conn = rank
    a = render.signature_text(impact(conn))
    b = render.signature_text(impact(conn))
    assert a == b
    assert json.dumps(render.signature_json(impact(conn))) == json.dumps(render.signature_json(impact(conn)))


def test_json_keeps_old_order_and_cap_and_adds_keys(rank):
    _, conn = rank
    r = impact(conn)
    out = render.signature_json(r)
    # today's order: certainty, then file and line; not ranked
    assert [(s["caller"]["name"], s["line"]) for s in out["call_sites"]] == [
        (s.caller.name, s.line) for s in r.sites
    ]
    assert out["call_sites"][0]["caller"]["name"] == "ignored_first"
    assert out["call_sites_total"] == 28 and out["call_sites_omitted"] == 0  # default cap stays 50
    assert out["return_value_uses"] == {u: c for u, c in r.uses().items()}
    assert out["summary"]["by_certainty"] == {"exact": 25, "probable": 3, "unknown": 0}
    # every caller whose path has a pointer call on it, not only those in pointer groups
    assert out["summary"]["upstream_through_a_pointer_call"] == 9
    assert [g["kind"] for g in out["upstream_groups"]] == ["direct", "pointer", "pointer"]
    assert out["upstream_groups"][2]["pointer_call_lines"] == [7, 8]
    small = render.signature_json(r, limit=3)
    assert len(small["call_sites"]) == 3 and small["call_sites_omitted"] == 25
    assert small["upstream_groups"][2]["members_omitted"] == 3
    assert len(render.signature_json(r, limit=3, show_all=True)["call_sites"]) == 28


def test_cli_flags(rank, capsys):
    root, _ = rank
    assert main(["signature", F, "--root", str(root)]) == 0
    default = capsys.readouterr().out
    assert len(site_rows(default, "Call sites (28)")) == 16
    assert main(["signature", F, "--root", str(root), "--all"]) == 0
    assert len(site_rows(capsys.readouterr().out, "Call sites (28)")) == 28
    assert main(["signature", F, "--root", str(root), "--limit", "3"]) == 0
    assert (
        len(site_rows(capsys.readouterr().out, "Call sites (28)")) == 4
    )  # 3 + the call that does not compile
    for bad in (["--all", "--limit", "3"], ["--limit", "0"]):
        with pytest.raises(SystemExit):
            main(["signature", F, "--root", str(root), *bad])
        capsys.readouterr()
    assert main(["signature", F, "--root", str(root), "--json", "--all"]) == 0
    assert len(json.loads(capsys.readouterr().out)["call_sites"]) == 28


def test_one_more_is_singular(rank):
    _, conn = rank
    text = render.signature_text(impact(conn), limit=1)
    assert "  ... and 1 more place (use --all" in text
    assert "  ... and 3 more pointer calls (use --all" in text


def test_no_legend_for_tags_that_are_all_hidden(rank, monkeypatch):
    _, conn = rank
    monkeypatch.setattr(render, "GROUP_HEADER_CAP", 1)
    text = render.signature_text(impact(conn), limit=1)  # one group, one member: top (exact)
    assert "[via_pointer]" not in text
    assert "[via_pointer] = " in render.signature_text(impact(conn))


def test_per_file_line_is_capped_but_complete_with_all(rank, monkeypatch):
    _, conn = rank
    monkeypatch.setattr(render, "FILES_CAP", 1)
    r = impact(conn)
    assert (
        "Call sites per file, most first: src/a_first.c 25, ... and 2 more files." in render.signature_text(r)
    )
    full = render.signature_text(r, show_all=True)
    assert "Call sites per file, most first: src/a_first.c 25, src/m_use.c 2, src/z_last.c 1." in full
    assert len(render.signature_json(r)["summary"]["call_sites_per_file"]) == 3
