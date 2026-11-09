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
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
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


def test_adding_a_parameter_to_a_cpp_method_keeps_its_callers_in_diff(repo_factory, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    root = repo_factory("cpp_virtual")
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "--no-verify", "-m", "init")
    build_index(root, jobs=1)
    # area() becomes area(int unit) everywhere; the two callers still call area()
    for rel in ("shapes.hpp", "shapes.cpp"):
        p = root / rel
        p.write_text(p.read_text().replace("area() const", "area(int unit) const"))
    outliner = Outliner(root)
    diffs, desc, old_outlines = diff.load_git(root, "HEAD", outliner)
    conn, _ = fresh_index(root, want=diff.wanted(diffs, "git"))
    res = diff.analyse(conn, root, diffs, mode="git", base=desc, old=old_outlines, now=outliner.now)
    assert {"Square::area", "Circle::area"} <= {c.fn.qualified_name for c in res.changed}
    # before the fix both vanished: in C++ a call that does not compile was not a call at all
    assert {"total", "fixed"} <= {h.fn.name for h in res.affected}
    conn.close()


# ------------------------------------------------ tricky forms (review of #17): C++, rarer C forms, headers


def broken_repo(repo_factory, tmp_path):
    ext = tmp_path / "outside_include"
    ext.mkdir()
    (ext / "ext_lib.h").write_text("int ext_add(int a, int b);\n")
    root = repo_factory("broken_calls", extra_args=[f"-I{ext}"])
    build_index(root, jobs=1)
    return store.connect(root)


def call_edges(conn, file):
    """{(caller, callee and its signature, confidence, line)} for calls made in `file`."""
    rows = conn.execute(
        """SELECT s.qualified_name, d.qualified_name, d.signature, e.confidence, e.line
           FROM edges e JOIN functions s ON s.id = e.src JOIN functions d ON d.id = e.dst
           WHERE e.kind = 'calls' AND s.file = ?""",
        (file,),
    )
    return {(a, f"{b}: {sig}", c, line) for a, b, sig, c, line in rows}


def taken(conn):
    sql = "SELECT f.qualified_name FROM address_taken a JOIN functions f ON f.id = a.function_id"
    return {r[0] for r in conn.execute(sql)}


def sites(conn, caller, file):
    """(callee, confidence, line, n_args, detail, via_type) for every call site in `caller`."""
    rows = conn.execute(
        """SELECT d.qualified_name, c.confidence, c.line, c.n_args, c.detail, c.via_type
           FROM call_sites c JOIN functions s ON s.id = c.caller LEFT JOIN functions d ON d.id = c.callee
           WHERE s.qualified_name = ? AND s.file = ?""",
        (caller, file),
    )
    return sorted((tuple("" if x is None else x for x in r) for r in rows), key=str)


def test_cpp_calls_that_do_not_compile_are_calls(repo_factory, tmp_path):
    conn = broken_repo(repo_factory, tmp_path)
    e = call_edges(conn, "src/forms.cpp")
    pair = "pair: int pair(int, int)"
    for caller, callee, line in (
        ("bad_free", pair, 26),
        ("bad_zero_args", pair, 27),
        ("bad_too_many", "none: int none()", 28),
        ("bad_ignored", pair, 29),
        ("bad_template", "tf: int tf(T, T)", 31),
        ("bad_qualified", "ns::nf: int nf(int, int)", 32),
        ("bad_method", "Box::scale: int scale(int, int)", 33),
        ("bad_method_ptr", "Box::scale: int scale(int, int)", 34),
        ("bad_static", "Box::sm: int sm(int, int)", 36),
        ("bad_virtual", "Box::area: int area(int)", 37),
        ("bad_paren", pair, 38),
        ("bad_macro", pair, 39),
        ("bad_nested", pair, 40),
    ):
        assert (caller, callee, "exact", line) in e, caller
    # no overload of ov / Box::mo matches: every candidate is linked, and the link says it may be either one
    assert {x for x in e if x[0] in ("bad_overload", "bad_overloaded_method")} == {
        ("bad_overload", "ov: int ov(int)", "ambiguous", 30),
        ("bad_overload", "ov: int ov(int, int)", "ambiguous", 30),
        ("bad_overloaded_method", "Box::mo: int mo(int)", "ambiguous", 35),
        ("bad_overloaded_method", "Box::mo: int mo(int, int)", "ambiguous", 35),
    }
    overload_sites = sites(conn, "bad_overload", "src/forms.cpp")
    assert [s[1] for s in overload_sites] == ["ambiguous", "ambiguous"]
    assert all(s[4].startswith("does not compile") and "2 functions named ov" in s[4] for s in overload_sites)
    # a broken call through a base pointer to a virtual method can still land on an override
    assert ("bad_virtual", "Square::area: int area(int)", "virtual", 37) in e
    # the outer call of pair(pair(1), 2, 3) is broken too, and the inner one is passed to it
    nested = sites(conn, "bad_nested", "src/forms.cpp")
    assert [s[4].startswith("does not compile") for s in nested] == [True, True]
    assert any(s[3] == 1 and s[4].endswith("; to pair() as argument 1") for s in nested)
    assert all(s[4].startswith("does not compile") for s in sites(conn, "bad_method", "src/forms.cpp"))
    conn.close()


def test_cpp_functions_passed_as_values_are_not_calls(repo_factory, tmp_path):
    conn = broken_repo(repo_factory, tmp_path)
    e = call_edges(conn, "src/forms.cpp")
    for caller in ("v_construct", "v_paren_init", "v_overload_init", "v_overload_one", "v_pass", "good_take"):
        assert not any(x[0] == caller and x[1].startswith(("pair:", "ov:")) for x in e), caller
    t = taken(conn)
    assert "pair" in t  # take(pair), T(pair), T x(pair, 1)
    assert not {"Box::scale", "Box::sm", "none", "ns::nf", "tf"} & t
    conn.close()


def test_cpp_function_used_as_a_value_in_broken_code_is_not_a_call(repo_factory, tmp_path):
    conn = broken_repo(repo_factory, tmp_path)
    values = ("value_int", "value_return", "value_overload", "value_macro", "value_std_function")
    for caller in values:
        assert sites(conn, caller, "src/forms.cpp") == [], caller
    assert not {x for x in call_edges(conn, "src/forms.cpp") if x[0] in values}
    # `int x = lone;` names lone as a value: it is address taken there
    sql = """SELECT g.qualified_name, a.line, a.how FROM address_sites a JOIN functions f ON f.id = a.function_id
             JOIN functions g ON g.id = a.in_function WHERE f.name = 'lone'"""
    assert sorted(tuple(r) for r in conn.execute(sql)) == [
        ("value_int", 63, "stored in x"),
        ("value_macro", 67, "stored in x"),
    ]
    # a zero-argument call inside a macro is still a call
    assert ("bad_macro_zero", "pair: int pair(int, int)", "exact", 81) in call_edges(conn, "src/forms.cpp")
    conn.close()


def test_cpp_broken_calls_through_std_function_or_member_pointer_are_pointer_calls(repo_factory, tmp_path):
    conn = broken_repo(repo_factory, tmp_path)
    for caller, line, n_args in (("bad_std_function", 78, 2), ("bad_member_pointer", 79, 1)):
        [site] = sites(conn, caller, "src/forms.cpp")
        assert site[1:4] == ("via_pointer", line, n_args), caller
        assert site[4].startswith("does not compile"), caller
        sql = """SELECT b.kind FROM blind_spots b JOIN functions f ON f.id = b.function_id
                 WHERE f.qualified_name = ? AND b.line = ?"""
        assert [tuple(r) for r in conn.execute(sql, (caller, line))] == [("pointer_call",)], caller
    conn.close()


def test_rarer_c_forms_of_a_broken_call(repo_factory, tmp_path):
    conn = broken_repo(repo_factory, tmp_path)
    e = call_edges(conn, "src/forms.c")
    assert ("bad_deref", "two: int two(int, int)", "exact", 9) in e
    assert ("bad_addr", "three: int three(int, int, int)", "exact", 10) in e
    # (c ? two : other2)(1) and p(1): calls through a pointer, linked to every function of that type
    # (pair in forms.cpp has that type too)
    assert {x for x in e if x[0] == "bad_cond"} == {
        ("bad_cond", "two: int two(int, int)", "via_pointer", 13),
        ("bad_cond", "other2: int other2(int, int)", "via_pointer", 13),
        ("bad_cond", "pair: int pair(int, int)", "via_pointer", 13),
    }
    assert ("bad_ptr", "other2: int other2(int, int)", "via_pointer", 18) in e
    [ptr_site] = sites(conn, "bad_ptr", "src/forms.c")
    assert ptr_site[1:4] == ("via_pointer", 18, 1) and ptr_site[4].startswith("does not compile")
    assert ptr_site[5] == "int (int, int)"
    # the address of two is taken only in bad_cond, never by the broken (*two)(1)
    sql = "SELECT a.line FROM address_sites a JOIN functions f ON f.id = a.function_id WHERE f.name = 'two'"
    assert {r[0] for r in conn.execute(sql)} == {13}
    assert {"two", "other2", "three"} <= taken(conn)  # three: passed as a value in passes_three
    # both calls in two(two(1), 2, 3) and in two(two(1), 2) do not compile
    for caller in ("bad_nested", "bad_inner"):
        assert [s[4].startswith("does not compile") for s in sites(conn, caller, "src/forms.c")] == [
            True,
            True,
        ], caller
    conn.close()


def test_broken_call_to_a_function_declared_outside_the_repo_but_defined_here(repo_factory, tmp_path):
    conn = broken_repo(repo_factory, tmp_path)
    e = call_edges(conn, "src/outside.c")
    want = {
        ("good_call", "ext_add: int ext_add(int, int)", "exact", 4),
        ("bad_call", "ext_add: int ext_add(int, int)", "exact", 5),
    }
    assert want <= e
    conn.close()
