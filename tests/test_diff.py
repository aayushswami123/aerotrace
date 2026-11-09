"""Diff impact: parsing diffs, mapping changed lines to code, and what a change could break."""

import io
import json
import os
import re
import subprocess

import pytest

from aerotrace.analysis import diff, render
from aerotrace.cli import main
from aerotrace.index import store
from aerotrace.index.build import build_index, fresh_index
from aerotrace.index.history import Outliner
from aerotrace.toolchain.git import GitError
from aerotrace.toolchain.unidiff import parse_unified
from conftest import GOLDEN

# ---------------------------------------------------------------- parsing (no libclang, no git)


def test_parse_git_diff_tracks_both_sides():
    text = """\
diff --git a/x.c b/x.c
index 1..2 100644
--- a/x.c
+++ b/x.c
@@ -3,2 +3 @@ int f(void) {
-    a();
--- not a header, a removed line
+    b();
@@ -10,0 +10,2 @@
+new1
+new2
"""
    [fd] = parse_unified(text)
    assert (fd.old_path, fd.new_path) == ("x.c", "x.c")
    assert fd.removed == [(3, 2), (4, 2)]  # removed between new lines 2 and 3
    assert fd.added == [(3, 4), (10, 10), (11, 10)]  # added after old line 10
    assert fd.old_text == {3: "    a();", 4: "-- not a header, a removed line"}


def test_parse_new_deleted_renamed_binary_and_quoted_files():
    text = """\
diff --git a/new.c b/new.c
new file mode 100644
--- /dev/null
+++ b/new.c
@@ -0,0 +1 @@
+int x;
diff --git a/gone.c b/gone.c
deleted file mode 100644
--- a/gone.c
+++ /dev/null
@@ -1 +0,0 @@
-int y;
\\ No newline at end of file
diff --git a/old name.c b/new name.c
similarity index 100%
rename from old name.c
rename to new name.c
diff --git a/img.png b/img.png
Binary files a/img.png and b/img.png differ
diff --git "a/caf\\303\\251.c" "b/caf\\303\\251.c"
--- "a/caf\\303\\251.c"
+++ "b/caf\\303\\251.c"
@@ -1 +1 @@
-a
+b
"""
    new, gone, renamed, binary, quoted = parse_unified(text)
    assert (new.old_path, new.new_path, new.added) == (None, "new.c", [(1, 0)])
    assert (gone.old_path, gone.new_path, gone.removed) == ("gone.c", None, [(1, 0)])
    assert (renamed.old_path, renamed.new_path) == ("old name.c", "new name.c")
    assert binary.binary
    assert quoted.new_path == "café.c"


def test_parse_plain_diff_u_with_timestamps():
    text = "--- lib/a.c\t2026-11-08 10:00:00\n+++ lib/a.c\t2026-11-08 10:01:00\n@@ -1 +1 @@\n-a\n+b\n"
    [fd] = parse_unified(text)
    assert (fd.old_path, fd.new_path) == ("lib/a.c", "lib/a.c")


def test_windows_line_endings_do_not_leak_into_paths():
    [fd] = parse_unified("--- a/x.c\r\n+++ b/x.c\r\n@@ -1 +1 @@\r\n-a\r\n+b\r\n")
    assert (fd.old_path, fd.new_path, fd.removed, fd.added) == ("x.c", "x.c", [(1, 0)], [(1, 1)])


def test_a_cut_off_diff_is_refused():
    with pytest.raises(ValueError, match="cut off"):
        parse_unified("--- a/x.c\n+++ b/x.c\n@@ -1,3 +1,3 @@\n a\n")


def test_ranges():
    assert diff.ranges([9, 1, 2, 3, 7, 10]) == "1-3, 7, 9-10"


# ---------------------------------------------------------------- git mode on a real repo


def git(root, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture(autouse=True)
def fixed_git_dates(monkeypatch):
    """Same commit ids on every run, so text answers can be compared exactly."""
    for k in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"):
        monkeypatch.setenv(k, "2026-01-01T00:00:00Z")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")


@pytest.fixture
def repo(repo_factory):
    root = repo_factory("c_multi")
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "--no-verify", "-m", "init")
    build_index(root, jobs=1)
    return root


def edit(root, rel, old, new):
    p = root / rel
    text = p.read_text()
    assert old in text, old
    p.write_text(text.replace(old, new, 1))


def run_git_mode(root, base="HEAD", depth=5):
    outliner = Outliner(root)
    diffs, desc, old = diff.load_git(root, base, outliner)
    conn, _ = fresh_index(root, want=diff.wanted(diffs, "git"))
    return diff.analyse(conn, root, diffs, mode="git", base=desc, depth=depth, old=old, now=outliner.now)


def affected(res):
    return {(h.fn.qualified_name, h.fn.file, h.confidence) for h in res.affected}


def test_clean_tree_changes_nothing(repo):
    res = run_git_mode(repo)
    assert res.changed == [] and res.affected == [] and res.outside == []


def test_body_edit_lists_callers_pointer_callers_and_data_readers(repo):
    edit(repo, "lib/shared.c", "g_total += x;", "g_total += 2 * x;")
    res = run_git_mode(repo)
    assert [(c.fn.qualified_name, c.state) for c in res.changed] == [("add", "changed")]
    assert affected(res) >= {
        ("on_a", "lib/table.c", "exact"),
        ("main", "prog_a/main.c", "exact"),
        ("dispatch", "lib/table.c", "via_pointer"),
        ("get", "lib/shared.c", "data"),  # reads g_total, which add writes
    }
    assert res.outside == [] and res.removed == []


def test_deleted_function_is_found_even_when_git_lines_it_up_with_its_neighbour(repo):
    # git shows this as add's "}" being removed together with get(); only the old version tells them apart
    edit(repo, "lib/shared.c", "int get(void) {\n    return g_total;\n}\n\n", "")
    res = run_git_mode(repo)
    [removed] = res.removed
    assert (removed.name, removed.file, removed.line_start, removed.line_end) == (
        "get",
        "lib/shared.c",
        9,
        11,
    )
    assert [c.file for c in removed.still_called_by] == ["prog_b/main.c"]
    assert ("main", "prog_b/main.c", "calls_removed") in affected(res)


def test_deleted_file_lists_its_functions_and_who_still_calls_them(repo):
    (repo / "prog_a" / "util.c").unlink()
    res = run_git_mode(repo)
    assert res.deleted == ["prog_a/util.c"]
    assert sorted(r.name for r in res.removed) == ["bump", "util_run"]
    assert ("main", "prog_a/main.c", "calls_removed") in affected(res)


def test_changed_global_declaration_affects_every_user(repo):
    edit(repo, "lib/shared.c", "int g_total;", "long g_total;")
    edit(repo, "include/shared.h", "extern int g_total;", "extern long g_total;")
    res = run_git_mode(repo)
    assert [(v.name, v.kind, v.users) for v in res.changed_vars] == [("g_total", "global", 2)]
    assert {("add", "lib/shared.c", "uses_changed"), ("get", "lib/shared.c", "uses_changed")} <= affected(res)
    assert res.outside == []  # the old copy of the changed line is the same declaration, not unknown code


def test_changed_prototype_in_a_header_marks_the_function(repo):
    edit(repo, "include/shared.h", "void add(int x);", "void add(int amount);")
    res = run_git_mode(repo)
    assert [c.fn.qualified_name for c in res.changed] == ["add"]
    assert res.outside == []


def test_a_macro_change_is_reported_as_not_followed(repo):
    # blind spot: AeroTrace does not track macro uses, so it must say so instead of answering "nothing"
    edit(repo, "include/shared.h", "#define SHARED_H\n", "#define SHARED_H\n#define LIMIT 10\n")
    res = run_git_mode(repo)
    assert res.changed == []
    assert [(o.file, o.lines) for o in res.outside] == [("include/shared.h", [3])]
    assert any("macro" in n for n in res.blind_spots)


def test_blank_lines_between_functions_are_not_noise(repo):
    edit(repo, "lib/shared.c", "}\n\nint get", "}\n\n\n\nint get")
    res = run_git_mode(repo)
    assert res.changed == [] and res.outside == []


def test_new_untracked_file_outside_the_compile_database_is_reported(repo):
    (repo / "lib" / "extra.c").write_text('#include "shared.h"\nint extra(void) { return get(); }\n')
    (repo / "notes.md").write_text("hi\n")
    res = run_git_mode(repo)
    assert res.not_indexed == ["lib/extra.c"]
    assert res.ignored == 1


def test_new_file_is_indexed_when_flags_are_guessed(repo_factory):
    root = repo_factory("c_multi", compdb=False)
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "--no-verify", "-m", "init")
    build_index(root, jobs=1)
    (root / "lib" / "extra.c").write_text('#include "shared.h"\nint extra(void) { return get(); }\n')
    res = run_git_mode(root)
    assert [(c.fn.qualified_name, c.state) for c in res.changed] == [("extra", "new")]
    assert res.not_indexed == []


def test_branch_base_compares_with_where_the_branch_left(repo):
    git(repo, "checkout", "-q", "-b", "feature")
    edit(repo, "lib/shared.c", "return ++id;", "return id += 2;")
    git(repo, "commit", "-q", "--no-verify", "-am", "change next_id")
    git(repo, "checkout", "-q", "main")
    edit(repo, "prog_b/util.c", "count += 2;", "count += 3;")
    git(repo, "commit", "-q", "--no-verify", "-am", "main moves on")
    git(repo, "checkout", "-q", "feature")
    res = run_git_mode(repo, "main")
    assert [c.fn.qualified_name for c in res.changed] == ["next_id"]  # main's own change is not ours
    assert "where this branch left main" in res.base


@pytest.mark.parametrize("bad", ["--output=/tmp/x", "nosuch", "", "a b"])
def test_bad_base_is_refused(repo, bad):
    with pytest.raises(GitError):
        diff.load_git(repo, bad)


def test_text_answer_matches_golden(repo):
    edit(repo, "lib/shared.c", "int get(void) {\n    return g_total;\n}\n\n", "")
    edit(repo, "lib/shared.c", "g_total += x;", "g_total += 2 * x;")
    edit(repo, "include/shared.h", "#define SHARED_H\n", "#define SHARED_H\n#define LIMIT 10\n")
    out = render.diff_text(run_git_mode(repo)) + "\n"
    out = re.sub(
        r"HEAD \([0-9a-f]{12}\)", "HEAD (<commit>)", out
    )  # the fixture's compile db holds a tmp path
    golden = GOLDEN / "diff_c_multi.txt"
    if os.environ.get("AEROTRACE_UPDATE_GOLDEN"):
        golden.write_text(out)
    assert out == golden.read_text()


# ---------------------------------------------------------------- patch mode


def patch_of(root, rel, old, new):
    """A unified diff for one edit, made without touching the file."""
    p = root / rel
    before = p.read_text()
    after = before.replace(old, new, 1)
    tmp = root.parent / "patch_tmp"
    (tmp / "a" / rel).parent.mkdir(parents=True, exist_ok=True)
    (tmp / "b" / rel).parent.mkdir(parents=True, exist_ok=True)
    (tmp / "a" / rel).write_text(before)
    (tmp / "b" / rel).write_text(after)
    r = subprocess.run(
        ["git", "diff", "--no-index", "--no-color", "--no-prefix", f"a/{rel}", f"b/{rel}"],
        cwd=tmp,
        capture_output=True,
        text=True,
    )
    return r.stdout


def run_patch_mode(root, text):
    diffs = diff.load_patch(root, text)
    conn, _ = fresh_index(root, want=diff.wanted(diffs, "patch"))
    outliner = Outliner(root)
    return diff.analyse(conn, root, diffs, mode="patch", now=outliner.now, patched=outliner.text)


def test_patch_places_the_new_text_of_a_changed_declaration(repo):
    # #13: the new side of a changed declaration line was listed as "could not follow"
    text = patch_of(repo, "lib/shared.c", "int g_total;", "long g_total;")
    res = run_patch_mode(repo, text)
    assert [v.name for v in res.changed_vars] == ["g_total"]
    assert res.outside == []


def test_patch_new_text_outside_everything_is_still_reported(repo):
    text = patch_of(repo, "lib/shared.c", "int g_total;", "int g_total;\n#define MORE 1")
    res = run_patch_mode(repo, text)
    assert [(o.file, o.other_side) for o in res.outside] != []


def test_patch_not_applied_yet_maps_to_the_current_code(repo):
    text = patch_of(repo, "prog_a/util.c", "    count++;\n", "    count += 5;\n")
    res = run_patch_mode(repo, text)
    assert [c.fn.qualified_name for c in res.changed] == ["bump"]
    assert ("util_run", "prog_a/util.c", "exact") in affected(res)
    assert (repo / "prog_a" / "util.c").read_text().count("count++") == 1  # nothing was written


def test_patch_removing_a_whole_function_says_so(repo):
    text = patch_of(
        repo, "lib/shared.c", "int likely_positive(int x) {", "int likely_positive_x;\nint unused(int x) {"
    )
    res = run_patch_mode(repo, text)
    assert [c.fn.qualified_name for c in res.changed] == ["likely_positive"]
    text = patch_of(repo, "prog_b/util.c", "static void bump(void) {\n    count += 2;\n}\n", "")
    res = run_patch_mode(repo, text)
    assert [(c.fn.qualified_name, c.state) for c in res.changed] == [("bump", "removed")]


def test_patch_replacing_a_function_head_labels_the_old_one_removed(repo):
    # #44: the old function is gone from the patched version, so it is removed, not changed
    text = patch_of(repo, "lib/shared.c", "int likely_positive(int x) {", "int unused(int x) {")
    res = run_patch_mode(repo, text)
    assert [(c.fn.qualified_name, c.state) for c in res.changed] == [("likely_positive", "removed")]


def test_stale_patch_is_skipped_not_mapped_to_wrong_lines(repo):
    text = patch_of(repo, "prog_a/util.c", "    count++;\n", "    count += 5;\n")
    edit(repo, "prog_a/util.c", "static int count;\n", "static int count;\n\n\n")  # lines move
    res = run_patch_mode(repo, text)
    assert res.mismatched == ["prog_a/util.c"] and res.changed == []


def test_patch_that_creates_a_file_is_reported(repo):
    text = "--- /dev/null\n+++ b/lib/extra.c\n@@ -0,0 +1 @@\n+int extra(void) { return 1; }\n"
    res = run_patch_mode(repo, text)
    assert res.new_files == ["lib/extra.c"] and res.changed == []


def test_text_that_is_not_a_diff_is_refused(repo):
    with pytest.raises(ValueError):
        diff.load_patch(repo, "please change add()")


# ---------------------------------------------------------------- CLI


def test_cli_diff_json_and_patch_from_stdin(repo, capsys, monkeypatch):
    edit(repo, "lib/shared.c", "g_total += x;", "g_total += 2 * x;")
    assert main(["diff", "--root", str(repo), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [f["name"] for f in out["changed_functions"]] == ["add"]
    assert out["total_affected"] == len(out["affected"]) + out["omitted"]

    git(repo, "checkout", "--", ".")
    store.connect(repo).close()
    monkeypatch.setattr(
        "sys.stdin", io.StringIO(patch_of(repo, "lib/shared.c", "return g_total;", "return 0;"))
    )
    assert main(["diff", "--root", str(repo), "--patch", "-"]) == 0
    assert "get  lib/shared.c:9" in capsys.readouterr().out


def test_cli_diff_outside_git_explains(tmp_path, capsys):
    assert main(["diff", "--root", str(tmp_path)]) == 2
    assert "not inside a git repository" in capsys.readouterr().err
