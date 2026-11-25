"""Last round of the review of #84 and #85: names in table and assembly files, binary hunks, constructors,
the help text and the wording of a file-scope part."""

from __future__ import annotations

import pytest

from aerotrace import ui
from aerotrace.analysis import check
from aerotrace.cli import main
from test_check import cxx as cxx_repo
from test_check import run_patch
from test_unknown_headline import H_GONE, contract_for, make_patch
from test_unknown_headline import unseen as unseen_repo

cxx = cxx_repo
unseen = unseen_repo


def quiet_out_c(root):
    """Make src/out.c stop naming h, so the only places that name it are the ones a test adds."""
    (root / "src/out.c").write_text("int out_user(void) {\n    return 0;\n}\n")


@pytest.mark.parametrize(
    ("rel", "text"),
    [
        ("src/x.def", "#ifdef Q\nint use_h(void) { return h(1); }\n#endif\n"),
        ("src/x.tbl", "ENTRY(h)\nint use_h(void) { return h(1); }\n"),
        ("src/x.S", ".globl _f\n_f:\n  bl _h\n  ret\n"),
        ("src/x.s", "f:\n  call h\n  ret\n"),
        ("src/x.asm", "  call _h\n"),
    ],
)
def test_a_removed_function_named_in_a_table_or_assembly_file_is_unknown(unseen, rel, text):
    quiet_out_c(unseen)
    (unseen / rel).write_text(text)
    c = contract_for(unseen, make_patch(unseen, H_GONE))
    assert c["verdict"] == "unknown" and check.exit_code(c) == 2, c["verdict"]
    assert rel in c["unchecked_reason"], c["unchecked_reason"]


def test_a_longer_name_in_assembly_is_not_a_hit(unseen):
    quiet_out_c(unseen)
    (unseen / "src/x.S").write_text("  bl _hash\n  bl foo_h\n")
    c = contract_for(unseen, make_patch(unseen, H_GONE))
    assert "x.S" not in (c["unchecked_reason"] or ""), c["unchecked_reason"]


BINARY = "diff --git a/src/lib.c b/src/lib.c\nindex 111..222 100644\nBinary files a/src/lib.c and b/src/lib.c differ\n"


@pytest.mark.parametrize("rel", ["src/lib.c", "include/lib.h", "src/x.def", "src/x.S", "Makefile"])
def test_a_binary_hunk_on_a_code_table_assembly_or_build_file_is_unknown(unseen, rel, capsys, tmp_path):
    text = BINARY.replace("src/lib.c", rel)
    c = contract_for(unseen, text)
    assert c["verdict"] == "unknown", c["verdict"]
    assert check.exit_code(c) == 2
    assert c["change"]["not_followed"]["no_text"] == [rel]
    gaps = " ".join(ui.terminal.not_followed_lines(c))
    assert f"{rel} changed but the patch has no text for it" in gaps, gaps
    if rel != "Makefile":  # a build file is also listed as a build file, first
        assert c["unchecked_reason"] == f"{rel} changed but the patch has no text for it"
    first = ui.render_text(c, unicode=True).splitlines()[0]
    assert "nothing to check" not in first and not first.startswith("✅"), first


def test_a_binary_file_that_is_not_code_stays_neutral(unseen):
    c = contract_for(unseen, BINARY.replace("src/lib.c", "logo.png"))
    assert c["verdict"] == "clear"
    assert "No C or C++ changes" in ui.render_text(c, unicode=True).splitlines()[0]


DROP_CTOR = ("X::X(int a) : k(a) {}\n\n", "")
GONE = ("X x(1);\n    return x.k;", "return 0;")


def test_a_removed_constructor_is_not_unknown_because_of_the_class_itself(cxx):
    _, c = run_patch(cxx, {"src/v.cpp": [DROP_CTOR], "src/use_ctor.cpp": [GONE]})
    assert c["verdict"] == "clear", c["unchecked_reason"]
    assert check.exit_code(c) == 0


def test_a_removed_constructor_used_in_a_file_outside_the_build_is_unknown(cxx):
    (cxx / "src/extra.cpp").write_text('#include "v.h"\nint extra() {\n    return X(3).k;\n}\n')
    _, c = run_patch(cxx, {"src/v.cpp": [DROP_CTOR], "src/use_ctor.cpp": [GONE]})
    assert c["verdict"] == "unknown" and check.exit_code(c) == 2
    assert "src/extra.cpp" in c["unchecked_reason"], c["unchecked_reason"]


def test_check_help_says_2_also_covers_a_check_that_could_not_run(capsys):
    with pytest.raises(SystemExit):
        main(["check", "--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert "could not run the check at all (bad patch file, bad git ref, no index)" in out


GLOBAL_DEF = [
    ("src/lib.c", "int f(int a) {", "int hits = 0;\n\nint f(int a) {"),
]


def test_a_file_scope_definition_is_named_in_the_reason(unseen):
    c = contract_for(unseen, make_patch(unseen, GLOBAL_DEF))
    assert c["verdict"] == "unknown"
    assert (
        "the declaration of hits (file-scope code is not followed) in src/lib.c" in c["unchecked_reason"]
    ), c["unchecked_reason"]


@pytest.mark.parametrize(
    "use", ["X x(1);\n    return x.k;", "X x{1};\n    return x.k;", "X* p = new X(2);\n    return p->k;"]
)
def test_a_removed_constructor_used_in_any_form_outside_the_build_is_unknown(cxx, use):
    (cxx / "src/extra.cpp").write_text('#include "v.h"\nint extra() {\n    ' + use + "\n}\n")
    _, c = run_patch(cxx, {"src/v.cpp": [DROP_CTOR], "src/use_ctor.cpp": [GONE]})
    assert c["verdict"] == "unknown" and check.exit_code(c) == 2, c["verdict"]
    assert "src/extra.cpp" in c["unchecked_reason"], c["unchecked_reason"]


def test_a_removed_destructor_named_in_a_file_outside_the_build_is_unknown(cxx):
    (cxx / "src/extra.cpp").write_text('#include "v.h"\nint extra() {\n    X x(1);\n    return x.k;\n}\n')
    _, c = run_patch(cxx, {"src/v.cpp": [("X::~X() {}\n", "")]})
    assert c["verdict"] == "unknown" and "src/extra.cpp" in c["unchecked_reason"], c["unchecked_reason"]


@pytest.mark.parametrize(
    "line",
    ["int a = 0, hits = 0;", "int (*handler)(int);", "int *p = 0, q = 1;"],
)
def test_an_ambiguous_file_scope_line_gets_the_generic_reason(unseen, line):
    c = contract_for(
        unseen, make_patch(unseen, [("src/lib.c", "int f(int a) {", line + "\n\nint f(int a) {")])
    )
    assert c["verdict"] == "unknown"
    assert c["unchecked_reason"] == "code outside any function in src/lib.c", c["unchecked_reason"]
