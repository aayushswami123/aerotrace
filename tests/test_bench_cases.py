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
from test_check import nodes, patch


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
