"""The four wrong "safe" answers of benchmark run 1 (redis-05, curl-05, redis-07, curl-07), as fixtures.

Each one was answered "None is sure to break" while the build failed. The Redis cases are also golden
facts on the pinned Redis (tests/realrepo/facts_redis.py); curl is not pinned, so its shapes live here.
"""

from __future__ import annotations

import pytest

from aerotrace.analysis import check, diff
from aerotrace.index.build import build_index, fresh_index
from aerotrace.index.history import Outliner
from aerotrace.ui import terminal
from test_check import git, nodes, patch, run_git


@pytest.fixture
def repo(repo_factory):
    root = repo_factory("c_bench_cases")
    build_index(root, jobs=1)
    return root


def run(root, edits):
    diffs = diff.load_patch(root, patch(root, edits))
    conn, _ = fresh_index(root, want=diff.wanted(diffs, "patch"))
    res = check.run(conn, root, diffs, mode="patch", outliner=Outliner(root))
    return check.to_contract(conn, res, "aerotrace test")


def headline(c) -> str:
    return terminal.render(c).splitlines()[0]


def test_curl_05_removed_static_function_breaks_the_filter_table(repo):
    c = run(repo, {"src/filter.c": [("static void filter_destroy(int x) { (void)x; }\n", "")]})
    got = nodes(c)
    assert got["http_filter"] == ("breaks", "exact"), got
    assert "None is sure to break" not in headline(c)
    assert check.exit_code(c) == 1


def test_redis_05_removed_function_breaks_a_table_in_another_file(repo):
    # the prototype stays in filter.h, so cmds.c compiles; the program no longer links
    c = run(repo, {"src/handlers.c": [("int get_keys(int n) { return n + 1; }\n", "")]})
    got = nodes(c)
    assert got["cmd_table"] == ("breaks", "exact"), got
    by = {n["name"]: n for n in c["nodes"]}
    assert "does not link" in by["cmd_table"]["why"]
    assert "None is sure to break" not in headline(c)


RETYPE = {"include/filter.h": [("    timediff_t upkeep_ms;\n", "    void *upkeep_ms;\n")]}


def test_curl_07_int_field_to_pointer_breaks_only_the_uses_that_do_not_build(repo):
    c = run(repo, RETYPE)
    got = nodes(c)
    # an error only in clang 16+ and GCC 14+, so probable; but a break, never "safe"
    assert got["conf_init"] == ("breaks", "probable"), got
    assert got["conf_due"][0] == "affected", got  # p != 0 still builds
    assert got["conf_reset"][0] == "affected", got  # p = 0 is a null pointer constant
    assert "None is sure to break" not in headline(c)
    assert check.exit_code(c) == 1


def test_uses_in_files_not_compiled_are_unknown_never_affected(repo, monkeypatch):
    # blind spot: when the users' files can't be compiled with the change, AeroTrace can't say they still
    # build, so their uses are unknown and the headline is not a "safe" one
    monkeypatch.setattr(check, "USER_FILES_COMPILED", 0)
    c = run(repo, RETYPE)
    got = nodes(c)
    for name in ("conf_init", "conf_due", "conf_reset"):
        assert got[name] == ("unknown", "unknown"), (name, got)
    assert any("were not compiled with the change" in b["text"] for b in c["sections"]["blind_spots"])
    assert "None is sure to break" not in headline(c)


# ---------------------------------------------------------------- review of #77


def test_an_initializer_that_sets_a_retyped_field_is_compiled(repo):
    c = run(repo, RETYPE)
    assert nodes(c)["g_conf"] == ("breaks", "probable"), nodes(c)


def test_a_file_that_already_fails_gives_unknown_not_still_compiles(repo):
    # blind spot: broken.c fails before the change (missing header), so a new error may be hidden
    c = run(repo, RETYPE)
    by = {n["name"]: n for n in c["nodes"]}
    assert (by["read_e"]["status"], by["read_e"]["certainty"]) == ("unknown", "unknown"), by["read_e"]
    assert "already had" in by["read_e"]["why"] and "still compiles" not in by["read_e"]["why"]


def test_an_error_only_gcc_14_gives_is_a_probable_break(repo):
    c = run(repo, {"include/filter.h": [("    int *counter;\n", "    long *counter;\n")]})
    by = {n["name"]: n for n in c["nodes"]}
    assert (by["conf_counter"]["status"], by["conf_counter"]["certainty"]) == ("breaks", "probable")
    assert "GCC 14+" in by["conf_counter"]["why"]


def test_a_moved_function_is_not_a_break(repo):
    c = run(
        repo,
        {
            "src/handlers.c": [("int get_keys(int n) { return n + 1; }\n", "")],
            "src/cmds.c": [
                (
                    "struct cmd cmd_table[] = {",
                    "int get_keys(int n) { return n + 1; }\n\nstruct cmd cmd_table[] = {",
                )
            ],
        },
    )
    got = nodes(c)
    assert got.get("cmd_table", ("affected",))[0] != "breaks", got
    assert check.exit_code(c) == 0, c["summary"]


def test_git_mode_removed_function_in_a_table_is_never_an_all_clear(repo):
    # blind spot: in git mode the index no longer links the table to the removed function; the answer
    # must say so instead of an all-clear
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-verify", "-m", "init")
    build_index(repo, jobs=1)
    _, c = run_git(repo, {"src/handlers.c": [("int get_keys(int n) { return n + 1; }\n", "")]})
    assert headline(c).startswith(("ℹ", "⚠")), headline(c)  # noqa: RUF001 (the info emoji)
    assert any("get_keys was removed" in b["text"] for b in c["sections"]["blind_spots"])
