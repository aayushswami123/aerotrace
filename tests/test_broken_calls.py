"""Calls that don't compile (#14): still calls, never 'address taken'. They matter most right after a
signature change in the working tree, when every old caller has the wrong number of arguments."""

import subprocess

from aerotrace.analysis import diff, graph, symbols
from aerotrace.index import store
from aerotrace.index.build import build_index, fresh_index
from aerotrace.index.history import Outliner


def one(conn, query):
    [f] = symbols.find(conn, query)
    return f


def test_a_call_with_too_few_arguments_is_still_a_caller(repo_factory):
    root = repo_factory("c_data")
    build_index(root, jobs=1)
    conn = store.connect(root)
    w = graph.walk(conn, one(conn, "wants_two"), "callers", 1)
    assert [(h.fn.name, h.confidence, h.lines) for h in w.hits] == [("calls_wants_two", "exact", [16])]
    # wants_two is only called (badly), never used as a value: it must not be linked to pointer calls
    taken = {
        r[0]
        for r in conn.execute("SELECT f.name FROM address_taken a JOIN functions f ON f.id = a.function_id")
    }
    assert "wants_two" not in taken
    assert "too_few_args" in {h.fn.name for h in graph.walk(conn, one(conn, "parse_num"), "callers", 1).hits}
    conn.close()


def git(root, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


def test_adding_a_parameter_keeps_the_old_callers_in_diff(repo_factory, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    root = repo_factory("c_multi")
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "--no-verify", "-m", "init")
    build_index(root, jobs=1)
    # add(int x) becomes add(int x, int scale); none of its three callers is updated
    for rel, old, new in (
        (
            "lib/shared.c",
            "void add(int x) {\n    g_total += x;",
            "void add(int x, int scale) {\n    g_total += x * scale;",
        ),
        ("include/shared.h", "void add(int x);", "void add(int x, int scale);"),
    ):
        p = root / rel
        p.write_text(p.read_text().replace(old, new))
    outliner = Outliner(root)
    diffs, desc, old_outlines = diff.load_git(root, "HEAD", outliner)
    conn, _ = fresh_index(root, want=diff.wanted(diffs, "git"))
    res = diff.analyse(conn, root, diffs, mode="git", base=desc, old=old_outlines, now=outliner.now)
    assert [c.fn.name for c in res.changed] == ["add"]
    affected = {(h.fn.name, h.fn.file, h.distance) for h in res.affected}
    # before the fix these three vanished: their calls no longer compile, so they were not calls at all
    assert {("main", "prog_a/main.c", 1), ("on_a", "lib/table.c", 1), ("on_b", "lib/table.c", 1)} <= affected
    conn.close()
