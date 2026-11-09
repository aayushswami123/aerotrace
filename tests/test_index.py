import os
import sqlite3

import pytest

from aerotrace.index import store
from aerotrace.index.build import build_index
from aerotrace.index.merge import _Symbols
from conftest import GOLDEN


def index(root, **kw):
    build_index(root, jobs=1, **kw)
    return store.connect(root)


def edges(conn, kind=None):
    """{(src name@file, dst name@file, kind, confidence)} for easy assertions."""
    name = {}
    for t in ("functions", "variables"):
        for r in conn.execute(f"SELECT id, qualified_name, file FROM {t}"):
            name[r["id"]] = f"{r['qualified_name']}@{r['file']}"
    rows = conn.execute("SELECT src, dst, kind, confidence FROM edges").fetchall()
    return {(name[r[0]], name[r[1]], r[2], r[3]) for r in rows if kind is None or r[2] == kind}


def check_golden(conn, name):
    got = store.dump_canonical(conn)
    path = GOLDEN / f"{name}.txt"
    if os.environ.get("AEROTRACE_UPDATE_GOLDEN"):
        path.write_text(got)
    assert got == path.read_text(), f"answers changed; see CONTRIBUTING.md before updating {path.name}"


# ---------------------------------------------------------------- C


@pytest.fixture
def c_conn(repo_factory):
    return index(repo_factory("c_multi"))


def test_c_matches_golden(c_conn):
    check_golden(c_conn, "c_multi")


def test_two_mains_stay_two_functions(c_conn):
    mains = c_conn.execute("SELECT file, clash FROM functions WHERE name = 'main' ORDER BY file").fetchall()
    assert [tuple(r) for r in mains] == [("prog_a/main.c", 1), ("prog_b/main.c", 1)]
    e = edges(c_conn, "calls")
    assert ("main@prog_a/main.c", "util_run@prog_a/util.c", "calls", "exact") in e
    assert ("main@prog_b/main.c", "util_run_b@prog_b/util.c", "calls", "exact") in e
    assert not any(s == "main@prog_a/main.c" and d.startswith("util_run_b") for s, d, _, _ in e)


def test_static_functions_in_files_with_the_same_name_stay_apart(c_conn):
    e = edges(c_conn)
    assert ("util_run@prog_a/util.c", "bump@prog_a/util.c", "calls", "exact") in e
    assert ("util_run_b@prog_b/util.c", "bump@prog_b/util.c", "calls", "exact") in e
    assert ("bump@prog_a/util.c", "count@prog_a/util.c", "writes", "exact") in e
    assert ("bump@prog_b/util.c", "count@prog_b/util.c", "writes", "exact") in e
    assert ("bump@prog_a/util.c", "count@prog_b/util.c", "writes", "exact") not in e


def test_globals_and_static_locals_are_tracked(c_conn):
    e = edges(c_conn)
    assert ("add@lib/shared.c", "g_total@lib/shared.c", "writes", "exact") in e
    assert ("get@lib/shared.c", "g_total@lib/shared.c", "reads", "exact") in e
    assert ("next_id@lib/shared.c", "next_id::id@lib/shared.c", "writes", "exact") in e
    # `extern int g_total;` in the header is the same variable, not a clash
    assert c_conn.execute("SELECT COUNT(*) FROM variables WHERE name = 'g_total'").fetchone()[0] == 1


def test_blind_spots_are_recorded_not_guessed(c_conn):
    rows = c_conn.execute(
        "SELECT f.name, b.kind FROM blind_spots b JOIN functions f ON f.id = b.function_id ORDER BY 1"
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        ("apply", "pointer_call"),
        ("calls_missing", "unresolved_call"),
        ("dispatch", "pointer_call"),
    ]
    taken = c_conn.execute(
        "SELECT f.name FROM address_taken a JOIN functions f ON f.id = a.function_id ORDER BY 1"
    ).fetchall()
    assert [r[0] for r in taken] == ["on_a", "on_b"]


def test_index_is_identical_across_folders_and_runs(tmp_path, repo_factory):
    from conftest import make_repo

    a = index(make_repo(tmp_path / "one", "c_multi"), use_cache=False)
    b = index(make_repo(tmp_path / "two", "c_multi"), use_cache=False)
    assert store.dump_canonical(a) == store.dump_canonical(b)


def test_cache_reuses_unchanged_files_and_reparses_changed_ones(repo_factory):
    root = repo_factory("c_multi")
    first = build_index(root, jobs=1)
    assert first.cache_hits == 0
    again = build_index(root, jobs=1)
    assert again.cache_hits == again.units
    (root / "prog_a" / "util.c").write_text((root / "prog_a" / "util.c").read_text() + "\n")
    third = build_index(root, jobs=1)
    assert third.cache_hits == third.units - 1


def test_status_reports_changed_files(repo_factory):
    root = repo_factory("c_multi")
    conn = index(root)
    assert store.changed_files(conn, root) == []
    (root / "include" / "shared.h").write_text("// edited\n" + (root / "include" / "shared.h").read_text())
    assert store.changed_files(conn, root) == ["include/shared.h"]


def test_index_folder_ignores_itself_in_git(repo_factory):
    root = repo_factory("c_multi")
    index(root)
    assert (root / ".aerotrace" / ".gitignore").read_text().splitlines()[-1] == "*"


def test_parallel_build_matches_single_process(tmp_path):
    from conftest import make_repo

    one = make_repo(tmp_path / "a", "c_multi")
    many = make_repo(tmp_path / "b", "c_multi")
    build_index(one, jobs=1)
    build_index(many, jobs=3)
    assert store.dump_canonical(store.connect(one)) == store.dump_canonical(store.connect(many))


def test_without_compile_database_c_is_still_parsed_as_c(repo_factory):
    root = repo_factory("c_multi", compdb=False)
    report = build_index(root, jobs=1)
    assert report.flags_source == "guessed"
    assert report.files_with_errors == 0


def test_old_schema_is_refused(repo_factory):
    root = repo_factory("c_multi")
    index(root)
    with sqlite3.connect(store.db_path(root)) as raw:
        raw.execute("UPDATE meta SET value = '0' WHERE key = 'schema_version'")
    with pytest.raises(FileNotFoundError):
        store.connect(root)


# ---------------------------------------------------------------- merge rules


def test_reference_to_a_clash_the_unit_cannot_see_is_ambiguous():
    s = _Symbols()
    s.defs["u"] = {"a.c": {}, "b.c": {}}
    assert s.resolve("u", {"a.c"}) == [("u@a.c", "exact")]
    assert s.resolve("u", {"c.c"}) == [("u@a.c", "ambiguous"), ("u@b.c", "ambiguous")]
    assert s.resolve("missing", set()) == []


# ---------------------------------------------------------------- C++


@pytest.fixture
def cpp_conn(repo_factory):
    return index(repo_factory("cpp_virtual"))


def test_cpp_matches_golden(cpp_conn):
    check_golden(cpp_conn, "cpp_virtual")


def test_virtual_call_may_land_on_every_override(cpp_conn):
    e = edges(cpp_conn, "calls")
    assert ("total@shapes.cpp", "Shape::area@shapes.hpp", "calls", "exact") in e
    assert ("total@shapes.cpp", "Square::area@shapes.cpp", "calls", "virtual") in e
    assert ("total@shapes.cpp", "Circle::area@shapes.cpp", "calls", "virtual") in e


def test_qualified_call_does_not_dispatch(cpp_conn):
    out = {(d, c) for s, d, _, c in edges(cpp_conn, "calls") if s == "fixed@shapes.cpp"}
    assert out == {("Square::area@shapes.cpp", "exact")}


def test_constructor_initializer_writes_the_field(cpp_conn):
    assert ("Square::Square@shapes.hpp", "Square::side_@shapes.hpp", "writes", "exact") in edges(cpp_conn)


def test_compiler_builtins_are_external_not_unresolved(c_conn):
    row = c_conn.execute("SELECT id, external_calls FROM functions WHERE name = 'likely_positive'").fetchone()
    assert row["external_calls"] == 1
    assert (
        c_conn.execute("SELECT COUNT(*) FROM blind_spots WHERE function_id = ?", (row["id"],)).fetchone()[0]
        == 0
    )


def test_pointer_calls_only_match_functions_of_the_same_type(c_conn):
    rows = c_conn.execute(
        """SELECT f.name, b.detail FROM blind_spots b JOIN functions f ON f.id = b.function_id
           WHERE b.kind = 'pointer_call' ORDER BY 1"""
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        ("apply", "call through a pointer to 'int (int)'; no function of that type has its address taken"),
        ("dispatch", "call through a pointer to 'void (void)'; may reach 2 functions of that type"),
    ]
    assert not any(src.startswith("apply@") for src, _, _, _ in edges(c_conn, "calls"))


def _age_index(root, version="2"):
    raw = sqlite3.connect(store.db_path(root))
    raw.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (version,))
    raw.commit()
    raw.close()


def test_an_old_index_is_rebuilt_with_a_clear_note_and_its_settings(repo_factory, capsys):
    from aerotrace.cli import main
    from aerotrace.index.build import fresh_index

    root = repo_factory("c_multi")
    build_index(root, jobs=1, excludes=["prog_b/util.c"])
    _age_index(root)
    conn, note = fresh_index(root)  # what the MCP server and `diff` do
    assert note.startswith("(Rebuilt the index: the index in ")
    assert "was made by an older AeroTrace (index format 2; this version uses 3)" in note
    assert store.read_meta(conn)["excludes"] == '["prog_b/util.c"]'  # the old settings are kept
    conn.close()
    _age_index(root)
    assert main(["callers", "add", "--root", str(root), "--depth", "1"]) == 0  # CLI queries rebuild too
    out = capsys.readouterr()
    assert "rebuilding it now" in out.err and out.out.startswith("Callers of add")


def test_a_newer_or_damaged_index_is_rebuilt_not_a_crash(repo_factory):
    from aerotrace.index.build import fresh_index

    root = repo_factory("c_multi")
    build_index(root, jobs=1)
    _age_index(root, "99")
    conn, note = fresh_index(root)
    assert "was made by a newer AeroTrace (index format 99" in note
    conn.close()
    store.db_path(root).write_bytes(b"not a database")
    with pytest.raises(store.IndexVersionError, match="can't be read"):
        store.connect(root)
    conn, note = fresh_index(root)
    assert "can't be read" in note and store.read_meta(conn)["schema_version"] == store.SCHEMA_VERSION
    conn.close()


# ---------------------------------------------------------------- progress, stopping, crashes


@pytest.mark.parametrize(("jobs", "isolate"), [(1, False), (1, True), (2, False)])
def test_build_reports_progress_file_by_file(tmp_path, jobs, isolate):
    from conftest import make_repo

    root = make_repo(tmp_path / "p", "c_multi")
    plain = make_repo(tmp_path / "q", "c_multi")
    calls = []
    report = build_index(root, jobs=jobs, isolate=isolate, progress=calls.append)
    build_index(plain, jobs=1)
    phases = [p.phase for p in calls]
    assert phases[:2] == ["flags", "cache"] and phases[-1] == "save"
    assert phases.count("parse") == report.units  # one call per parsed file
    assert [p.done for p in calls] == sorted(p.done for p in calls)  # never goes down
    assert calls[-1].done == calls[-1].total == report.units
    # neither the callback nor the process pool changes the index
    assert store.dump_canonical(store.connect(root)) == store.dump_canonical(store.connect(plain))

    warm = []
    build_index(root, jobs=jobs, isolate=isolate, progress=warm.append)
    assert "parse" not in [p.phase for p in warm]
    assert warm[-1].reused == warm[-1].total == report.units


def test_stopped_build_writes_nothing(repo_factory):
    import threading

    from aerotrace.index.build import BuildStopped

    root = repo_factory("c_multi")
    stop = threading.Event()
    stop.set()
    with pytest.raises(BuildStopped):
        build_index(root, jobs=2, stop=stop)
    assert not (root / ".aerotrace" / "index.db").exists()


def crash_on_b(job):
    """A parse worker that dies on b.c (it runs in a child process)."""
    unit = job[0]
    if unit.file.endswith("b.c"):
        os._exit(3)
    return {"file": unit.file}


def test_parser_crash_names_the_files_and_keeps_finished_results(tmp_path):
    from aerotrace.index.build import ParserCrashed, _parse_in_pool
    from aerotrace.toolchain import libclang
    from aerotrace.toolchain.compdb import CompileUnit

    units = [CompileUnit(file=str(tmp_path / f"{c}.c"), directory=str(tmp_path)) for c in "abcdef"]
    done = []
    with pytest.raises(ParserCrashed) as e:
        _parse_in_pool(
            tmp_path,
            [(u, "key", []) for u in units],
            [],
            2,
            libclang.configure(),
            lambda u, key, res: done.append(res["file"]),
            None,
            work=crash_on_b,
        )
    assert "b.c" in e.value.files
    assert all(not f.endswith("b.c") for f in done)  # what did finish was handed back (and cached)


# ---------------------------------------------------------------- files edited during a build (#43)


def _two_files(root):
    root.mkdir()
    (root / "a.c").write_text("int helper(int x) { return x + 1; }\n")
    (root / "b.c").write_text("int helper(int x);\nint user(void) { return helper(2); }\n")


def _names(root):
    with store.connect(root) as conn:
        return sorted(r[0] for r in conn.execute("SELECT name FROM functions"))


@pytest.mark.parametrize("use_cache", [False, True])
def test_file_edited_between_parse_and_save_is_seen_as_changed(tmp_path, use_cache):
    from aerotrace.index.build import index_plan

    root = tmp_path / "repo"
    _two_files(root)
    if use_cache:
        build_index(root, jobs=1)  # so the next build takes both files from the cache

    def edit_now(p):
        if p.phase == "save":  # both files were read; the user saves b.c now
            (root / "b.c").write_text(
                "int helper(int x);\nint user(void) { return helper(2); }\nint user2(void) { return helper(3); }\n"
            )

    build_index(root, jobs=1, use_cache=use_cache, progress=edit_now)
    assert _names(root) == ["helper", "user"]  # the build saw the old b.c
    plan = index_plan(root)
    assert plan is not None and plan.changed == ("b.c",)  # ...and the index knows it is out of date
    build_index(root, jobs=1)
    assert _names(root) == ["helper", "user", "user2"]
    assert index_plan(root) is None


def test_parse_records_the_hash_of_what_libclang_read(tmp_path):
    from aerotrace.frontends.cpp import parse as parse_mod
    from aerotrace.toolchain import libclang
    from aerotrace.toolchain.compdb import CompileUnit

    libclang.configure()
    root = tmp_path / "repo"
    _two_files(root)
    (root / "h.h").write_text("int helper(int x);\n")
    (root / "b.c").write_text('#include "h.h"\nint user(void) { return helper(2); }\n')
    res = parse_mod.parse_unit(CompileUnit(str(root / "b.c"), str(root), ["-x", "c"]), root, [], [])
    assert res["deps"] == {r: parse_mod.file_hash(str(root / r)) for r in ("b.c", "h.h")}
    for r, (size, mtime) in res["stats"].items():
        st = (root / r).stat()
        assert (size, mtime) == (st.st_size, st.st_mtime_ns)
    # a file that differs from what was parsed is never trusted
    assert parse_mod.file_stamp(str(root / "h.h"), "not the parsed content") == (
        "",
        (root / "h.h").stat().st_size,
        -1,
    )
