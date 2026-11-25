"""A function removed from a .c file is REMOVED, with or without its prototype, in any hunk shape (A-01, A-02)."""

from __future__ import annotations

import difflib
import subprocess

import pytest

from aerotrace.analysis import check, diff
from aerotrace.hooks import claude
from aerotrace.index.build import build_index, fresh_index
from aerotrace.index.history import Outliner

H, C = "include/lib.h", "src/lib.c"


def git(root, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture(autouse=True)
def fixed_git_dates(monkeypatch):
    for k in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"):
        monkeypatch.setenv(k, "2026-01-01T00:00:00Z")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")


@pytest.fixture
def repo(repo_factory):
    root = repo_factory("c_removed_fn")
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "--no-verify", "-m", "init")
    build_index(root, jobs=1)
    return root


def after_edit(root, edits):
    """{file: new text}, from {file: [(old, new), ...]}."""
    out = {}
    for rel, pairs in edits.items():
        text = (root / rel).read_text()
        for old, new in pairs:
            assert old in text, old
            text = text.replace(old, new, 1)
        out[rel] = text
    return out


def difflib_patch(root, edits):
    out = ""
    for rel, text in after_edit(root, edits).items():
        before = (root / rel).read_text()
        out += "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True), text.splitlines(keepends=True), f"a/{rel}", f"b/{rel}"
            )
        )
    return out


def git_patch(root, edits):
    """The same change as `git diff` writes it (the files are restored afterwards)."""
    for rel, text in after_edit(root, edits).items():
        (root / rel).write_text(text)
    p = subprocess.run(["git", "diff", "--no-color"], cwd=root, check=True, capture_output=True, text=True)
    git(root, "checkout", "--", ".")
    return p.stdout


def check_patch(root, text):
    diffs = diff.load_patch(root, text)
    conn, _ = fresh_index(root, want=diff.wanted(diffs, "patch"))
    res = check.run(conn, root, diffs, mode="patch", outliner=Outliner(root))
    return res, check.to_contract(conn, res, "aerotrace test")


def nodes(contract, status=None):
    return {
        n["name"]: (n["status"], n["certainty"])
        for n in contract["nodes"]
        if status is None or n["status"] == status
    }


def removed_names(res):
    return sorted(c.fn.qualified_name for c in res.diff.changed if c.state == "removed")


SCALE = "int scale(int x) {\n    return x * 2;\n}\n"
TRIPLE = "int triple(int x) {\n    return x * 3;\n}\n"
GONE_SCALE = {C: [(SCALE, "")]}
GONE_SCALE_AND_PROTO = {**GONE_SCALE, H: [("int scale(int x);\n", "")]}
GONE_TRIPLE_AND_PROTO = {C: [(TRIPLE, "")], H: [("int triple(int x);\n", "")]}


def cli_first_line(root, text, tmp_path, capsys):
    from aerotrace.cli import main

    f = tmp_path / "p.diff"
    f.write_text(text)
    capsys.readouterr()
    code = main(["check", "--patch", str(f), "--root", str(root)])
    out = [
        ln for ln in capsys.readouterr().out.splitlines() if ln.strip() and not ln.startswith("(Re-indexed")
    ]
    return code, out[0]


def assert_scale_removed(res, c):
    assert removed_names(res) == ["scale"]
    assert nodes(c)["main"][0] == "breaks"
    assert check.exit_code(c) == 1


# ---------------------------------------------------------------- A-01


def test_removing_a_function_with_its_prototype_is_removed(repo):
    res, c = check_patch(repo, git_patch(repo, GONE_SCALE_AND_PROTO))
    assert_scale_removed(res, c)


def test_removing_a_function_without_its_prototype_is_still_removed(repo):
    res, c = check_patch(repo, git_patch(repo, GONE_SCALE))
    assert_scale_removed(res, c)


def test_removed_function_left_in_a_table_in_another_file_is_not_clean(repo, tmp_path, capsys):
    text = git_patch(repo, GONE_TRIPLE_AND_PROTO)
    res, c = check_patch(repo, text)
    assert removed_names(res) == ["triple"]
    # `ops` in src/table.c still names it: the table no longer links
    assert nodes(c, "breaks") == {"ops": ("breaks", "exact")}
    assert check.exit_code(c) == 1
    code, first = cli_first_line(repo, text, tmp_path, capsys)
    assert code == 1 and "can break" in first


def test_removing_only_the_prototype_makes_its_callers_may_break(repo, tmp_path, capsys):
    text = git_patch(repo, {H: [("int scale(int x);\n", "")]})
    res, c = check_patch(repo, text)
    assert removed_names(res) == []  # the definition stays
    assert nodes(c, "breaks") == {"main": ("breaks", "probable")}
    assert check.exit_code(c) == 1
    texts = [b["text"] for b in c["sections"]["blind_spots"]]
    assert any("prototype of scale" in t for t in texts)  # the limit line stays
    code, first = cli_first_line(repo, text, tmp_path, capsys)
    assert code == 1 and "MAY BREAK" in first


# ---------------------------------------------------------------- A-02


def test_shifted_hunk_and_git_hunk_of_one_deletion_give_one_answer(repo):
    shifted = difflib_patch(repo, GONE_SCALE)
    plain = git_patch(repo, GONE_SCALE)
    removed = [ln for ln in shifted.splitlines() if ln.startswith("-") and not ln.startswith("---")]
    assert removed != ["-" + ln for ln in SCALE.splitlines()]  # the layout really shifts the hunk
    a, ca = check_patch(repo, shifted)
    b, cb = check_patch(repo, plain)
    assert_scale_removed(a, ca)
    assert_scale_removed(b, cb)
    # a shifted hunk also lists the neighbour whose closing brace it removes ("keep": changed); the rest is equal
    assert {k: v for k, v in nodes(ca).items() if k != "keep"} == nodes(cb)
    assert ca["summary"]["breaks_exact"] == cb["summary"]["breaks_exact"]


def test_shifted_hunk_with_prototype_removed_too(repo):
    shifted = difflib_patch(repo, GONE_SCALE_AND_PROTO)
    res, c = check_patch(repo, shifted)
    assert_scale_removed(res, c)


def test_hook_sees_a_deleted_function(repo):
    from aerotrace.analysis import rules as rules_mod

    before = (repo / C).read_text()
    after = after_edit(repo, GONE_SCALE)[C]
    out = claude.check_edit(repo, C, before, after, rules_mod.Rules())
    assert out is not None
    t = out["hookSpecificOutput"]
    msg = t.get("additionalContext") or t.get("permissionDecisionReason")
    assert msg.startswith("AeroTrace: this edit breaks code: 1 sure break(s).")
    assert "[breaks, exact] main (src/main.c:2): calls scale, which this patch removes" in msg


# ---------------------------------------------------------------- review of #81: prototype gone, definition kept or moved

FIX = "tests/fixtures"


def repo_of(repo_factory, name):
    root = repo_factory(name)
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "--no-verify", "-m", "init")
    build_index(root, jobs=1)
    return root


def only(c, status="breaks"):
    return nodes(c, status)


def test_c_prototype_removed_caller_in_another_file_may_break(repo_factory, tmp_path, capsys):
    root = repo_of(repo_factory, "c_proto_gone")
    text = git_patch(root, {"lib.h": [("int scale(int x);\n", "")]})
    _, c = check_patch(root, text)
    assert only(c) == {"main": ("breaks", "probable")}
    assert check.exit_code(c) == 1
    assert any("prototype of scale" in b["text"] for b in c["sections"]["blind_spots"])
    code, first = cli_first_line(root, text, tmp_path, capsys)
    assert code == 1 and "MAY BREAK" in first


def test_cpp_overload_prototype_removed_caller_breaks(repo_factory):
    # the compiler proves it (no matching function), so it is a sure break, stronger than may-break
    root = repo_of(repo_factory, "cpp_proto_gone")
    text = git_patch(root, {"lib.h": [("int f(const char *s);\n", "")]})
    _, c = check_patch(root, text)
    assert only(c) == {"main": ("breaks", "exact")}
    assert check.exit_code(c) == 1


def test_prototype_removed_but_another_declaration_stays_is_not_flagged(repo_factory):
    root = repo_of(repo_factory, "c_proto_dup")  # main.c declares scale itself
    text = git_patch(root, {"lib.h": [("int scale(int x);\n", "")]})
    _, c = check_patch(root, text)
    assert only(c) == {}
    assert check.exit_code(c) == 0


def test_function_moved_with_its_prototype_removed_may_break_difflib(repo_factory, tmp_path, capsys):
    root = repo_of(repo_factory, "c_moved_fn")
    edits = {
        "lib.c": [(SCALE, "")],
        "lib.h": [("int scale(int x);\n", "")],
        "other.c": [("    return x;\n}\n", "    return x;\n}\n" + SCALE)],
    }
    text = difflib_patch(root, edits)
    _, c = check_patch(root, text)
    assert only(c) == {"main": ("breaks", "probable")}
    assert check.exit_code(c) == 1
    assert any("prototype of scale" in b["text"] for b in c["sections"]["blind_spots"])
    code, first = cli_first_line(root, text, tmp_path, capsys)
    assert code == 1 and "MAY BREAK" in first


NEW_FILE = '--- /dev/null\n+++ b/other.c\n@@ -0,0 +1,4 @@\n+#include "lib.h"\n+int scale(int x) {\n+    return x * 2;\n+}\n'


@pytest.mark.parametrize("shape", ["git", "difflib"])
def test_function_moved_to_a_new_file_may_break_and_says_why(repo_factory, shape):
    root = repo_of(repo_factory, "c_proto_gone")
    edits = {"lib.c": [(SCALE, "")]}
    text = (git_patch if shape == "git" else difflib_patch)(root, edits) + NEW_FILE
    res, c = check_patch(root, text)
    assert removed_names(res) == ["scale"]
    assert only(c) == {"main": ("breaks", "probable")}  # not "sure": the new file may not be built
    assert check.exit_code(c) == 1
    assert any("other.c" in b["text"] and "new" in b["text"] for b in c["sections"]["blind_spots"])


# ---------------------------------------------------------------- second review of #81

PROTO_GONE = {"lib.h": [("int scale(int x);\n", "")]}


def test_files_that_only_take_the_address_are_checked(repo_factory):
    root = repo_of(repo_factory, "c_proto_addr")  # tbl.c: a table names scale; g.c: `return &scale;`
    _, c = check_patch(root, git_patch(root, PROTO_GONE))
    assert {k: v[0] for k, v in only(c).items()} == {"table": "breaks", "get": "breaks"}
    assert check.exit_code(c) == 1


def many_callers(root, n, first_line='#include "lib.h"\n'):
    for i in range(n):
        (root / f"user{i:02d}.c").write_text(
            f"{first_line}int user{i:02d}(void) {{\n    return scale({i});\n}}\n"
        )
    git(root, "add", "-A")
    git(root, "commit", "-q", "--no-verify", "-m", "users")
    build_index(root, jobs=1)


def test_files_over_the_compile_cap_stay_may_break_with_their_own_reason(repo_factory):
    root = repo_factory("c_proto_gone")
    git(root, "init", "-q", "-b", "main")
    many_callers(root, 10)
    _, c = check_patch(root, git_patch(root, PROTO_GONE))
    why = {n["name"]: n["why"] for n in c["nodes"] if n["status"] == "breaks"}
    capped = {k: w for k, w in why.items() if "more than 8 files" in w}
    assert len(capped) == 3  # 11 caller files (main.c and 10 users), 8 compiled
    assert all("could not be compiled" not in w for w in capped.values())
    assert all(nodes(c)[k] == ("breaks", "probable") for k in capped)
    assert check.exit_code(c) == 1
    assert any("3 more files were not compiled" in b["text"] for b in c["sections"]["blind_spots"])


def test_a_caller_file_that_already_had_errors_stays_may_break(repo_factory):
    root = repo_factory("c_proto_gone")
    git(root, "init", "-q", "-b", "main")
    many_callers(root, 1, first_line='#include "lib.h"\n#include "missing.h"\n')
    _, c = check_patch(root, git_patch(root, PROTO_GONE))
    assert nodes(c, "breaks")["user00"] == ("breaks", "probable")
    why = next(n["why"] for n in c["nodes"] if n["name"] == "user00")
    assert "already had" in why and "error" in why


def test_prototype_limit_line_mentions_implicit_declarations_and_overloads(repo_factory):
    root = repo_of(repo_factory, "c_proto_gone")
    _, c = check_patch(root, git_patch(root, PROTO_GONE))
    text = next(b["text"] for b in c["sections"]["blind_spots"] if "prototype of scale" in b["text"])
    assert "implicit declaration" in text and "overload" in text


def new_file_patch(root, name, body):
    n = body.count("\n")
    return f"--- /dev/null\n+++ b/{name}\n@@ -0,0 +1,{n} @@\n" + "".join(
        f"+{ln}\n" for ln in body.splitlines()
    )


@pytest.mark.parametrize(
    "fixture, gone, new_name, new_body",
    [
        (
            "cpp_moved_new",
            {"lib.cpp": [("int scale(int x) { return x * 2; }\n", "")]},
            "other.cpp",
            "long scale(long x) { return x * 2; }\n",
        ),
        (
            "cpp_moved_new",
            {"lib.cpp": [("int scale(int x) { return x * 2; }\n", "")]},
            "other.cpp",
            "namespace { int scale(int x) { return x * 2; } }\n",
        ),
        (
            "c_proto_gone",
            {"lib.c": [(SCALE, "")]},
            "other.c",
            "static int scale(int x) {\n    return x * 2;\n}\n",
        ),
        ("c_proto_gone", {"lib.c": [(SCALE, "")]}, "other.c", "int scale(int x);\n"),
        ("c_proto_gone", {"lib.c": [(SCALE, "")]}, "other.c", "long scale(long x) {\n    return x * 2;\n}\n"),
    ],
    ids=["other_signature", "unnamed_namespace", "static", "prototype_only", "c_other_signature"],
)
def test_a_new_file_that_does_not_really_replace_it_keeps_breaks_exact(
    repo_factory, fixture, gone, new_name, new_body
):
    root = repo_of(repo_factory, fixture)
    text = git_patch(root, gone) + new_file_patch(root, new_name, new_body)
    _, c = check_patch(root, text)
    assert nodes(c, "breaks") == {"main": ("breaks", "exact")}
    assert check.exit_code(c) == 1
