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


def test_removed_function_left_in_a_table_in_another_file_is_not_clean(repo):
    res, c = check_patch(repo, git_patch(repo, GONE_TRIPLE_AND_PROTO))
    assert removed_names(res) == ["triple"]
    # `ops` in src/table.c still names it: that is a break (or at least a may-break), never a clean pass
    statuses = {s for s, _ in nodes(c).values()}
    assert statuses & {"breaks", "affected"}
    assert check.exit_code(c) == 1 or any("triple" in b["text"] for b in c["sections"]["blind_spots"])


def test_removing_only_the_prototype_is_never_a_clean_answer(repo):
    res, c = check_patch(repo, git_patch(repo, {H: [("int scale(int x);\n", "")]}))
    assert removed_names(res) == []  # the definition stays
    texts = [b["text"] for b in c["sections"]["blind_spots"]]
    # callers in other files that relied on the header may not compile: say so, by name
    assert any("prototype" in t and "scale" in t for t in texts)


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
    assert nodes(ca) == nodes(cb)


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
    assert "breaks" in msg
