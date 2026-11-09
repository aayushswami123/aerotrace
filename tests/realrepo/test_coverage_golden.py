"""Coverage golden facts on pinned Redis and SQLite. Opt-in: `uv run pytest -m realrepo_coverage -rs`.

Unlike the other golden tests, these BUILD the pinned repos (with --coverage) and run a few of their own
Tcl tests, through `aerotrace tests record` (the same code a user runs). That is test infrastructure only,
never something AeroTrace does on its own. They need make, a C compiler, gcov and tclsh, and for SQLite the
Tcl headers (macOS: the SDK's Tcl.framework; Linux: the tcl-dev package). About 2 minutes.

Every claim is checked against the source first (`_proofs`), including the test harness files, so a fact
can't silently pass because a helper runs the code.
"""

from __future__ import annotations

import platform
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
import repos

from aerotrace.analysis import coverage, symbols, testimpact
from aerotrace.index import store
from aerotrace.toolchain import testrun

pytestmark = pytest.mark.realrepo_coverage

TOOLS = ("make", "cc", "tclsh")


def _need_tools() -> None:
    missing = [t for t in TOOLS if not shutil.which(t)]
    if not (shutil.which("gcov") or shutil.which("llvm-cov")):
        missing.append("gcov")
    if missing:
        pytest.skip(f"needs {', '.join(missing)}")


def _line(root: Path, rel: str, n: int) -> str:
    return (root / rel).read_text(errors="replace").split("\n")[n - 1]


def _record(root: Path, toml: str) -> coverage.Coverage:
    cfg = root / ".aerotrace" / "tests.toml"
    cfg.parent.mkdir(exist_ok=True)
    cfg.write_text(toml)
    plan = testrun.make_plan(testrun.load_config(cfg), [])
    out, data = testrun.record(root, plan, 1, True, print, "golden")
    assert out.name == "tests.json" and data.complete
    return data


def _ask(root: Path, query: str) -> testimpact.TestImpact:
    conn = store.connect(root)
    try:
        [fn] = symbols.find(conn, query)
        return testimpact.for_function(conn, root, fn)
    finally:
        conn.close()


def _gcda(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*.gcda"))


_DIRS: list[Path] = []


def _short_dir(name: str) -> Path:
    """A temp folder with a short path: Redis's tests open Unix sockets under it, and macOS limits socket
    paths to 104 bytes (pytest's own temp folders are too deep)."""
    base = "/tmp" if Path("/tmp").is_dir() else None
    d = Path(tempfile.mkdtemp(prefix=f"atcov-{name}-", dir=base))
    _DIRS.append(d)
    return d


@pytest.fixture(scope="module", autouse=True)
def _cleanup():
    yield
    for d in _DIRS:
        shutil.rmtree(d, ignore_errors=True)
    _DIRS.clear()


# ---------------------------------------------------------------- Redis


@pytest.fixture(scope="module")
def redis():
    _need_tools()
    root = repos.unpack(repos.REDIS, _short_dir("redis"))
    _record(
        root,
        """version = 1
build = "make -C src -j4 MALLOC=libc gcov > /dev/null"
objects = ["src"]
tests = ["unit/expire", "unit/type/string"]
run = "./runtest --single {test} --clients 4"
inputs = ["tests/unit/expire.tcl", "tests/unit/type/string.tcl", "tests/support/*.tcl", "tests/helpers/*",
          "tests/test_helper.tcl"]
timeout = 300
""",
    )
    return root


def test_redis_proofs(redis):
    T = "src/t_string.c"
    assert "void psetexCommand(client *c) {" in _line(redis, T, 456)
    assert 'test "PSETEX can set sub-second expires" {' in _line(redis, "tests/unit/expire.tcl", 96)
    # unit/type/string never names PSETEX ...
    assert "psetex" not in (redis / "tests/unit/type/string.tcl").read_text().lower()
    # ... and the harness mentions it only in a key-routing list and in a fuzz helper that only the
    # corrupt-dump fuzzer calls.
    hits = []
    for p in sorted([redis / "tests/test_helper.tcl", *(redis / "tests").glob("support/*.tcl"),
                     *(redis / "tests").glob("helpers/*")]):  # fmt: skip
        for n, ln in enumerate(p.read_text(errors="replace").split("\n"), 1):
            if "psetex" in ln.lower():
                hits.append((p.relative_to(redis).as_posix(), n))
    assert hits == [("tests/support/cluster.tcl", 31), ("tests/support/util.tcl", 854)]
    assert "proc generate_fuzzy_traffic_on_key {key type duration} {" in _line(
        redis, "tests/support/util.tcl", 851
    )
    users = sorted(
        p.relative_to(redis).as_posix()
        for p in (redis / "tests").rglob("*.tcl")
        if "generate_fuzzy_traffic_on_key" in p.read_text(errors="replace")
    )
    assert users == ["tests/integration/corrupt-dump-fuzzer.tcl", "tests/support/util.tcl"]


def test_redis_expire_runs_psetex_and_string_does_not(redis):
    res = _ask(redis, "psetexCommand@src/t_string.c")
    got = {t.test: t.level for t in res.tests}
    assert got.get("unit/expire") == "exact"
    assert "unit/type/string" not in got
    assert res.coverage == "fresh"


def test_redis_recording_wrote_no_gcda_in_the_repo(redis):
    assert _gcda(redis) == []


# ---------------------------------------------------------------- SQLite


@pytest.fixture(scope="module")
def sqlite():
    _need_tools()
    root = repos.unpack(repos.SQLITE, _short_dir("sqlite"))
    src = repos.unpack(repos.SQLITE_SRC, _short_dir("sqlite-src"))
    bld = src.parent / "bld"
    bld.mkdir()
    tcl = ""
    sdk = Path("/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk/System/Library/Frameworks/Tcl.framework")
    if platform.system() == "Darwin" and sdk.is_dir():
        tcl = f" --with-tcl={sdk}"
    configure = (
        f'cd {bld} && CFLAGS="--coverage -O0 -g" LDFLAGS=--coverage {src}/configure{tcl} > configure.log 2>&1'
    )
    if subprocess.run(configure, shell=True).returncode != 0:
        pytest.skip(f"SQLite's configure failed (Tcl headers missing?); see {bld}/configure.log")
    data = _record(
        root,
        f"""version = 1
build = "make -C {bld} -j4 testfixture > /dev/null 2>&1"
objects = ["{bld}"]
tests = ["select1"]
run = "{bld}/testfixture {src}/test/{{test}}.test > /dev/null"
inputs = []
timeout = 600
""",
    )
    return root, src, bld, data


def test_sqlite_proofs(sqlite):
    root, src, bld, _ = sqlite
    assert "SQLITE_PRIVATE int sqlite3Select(" in _line(root, "sqlite3.c", 157288)
    assert "static void jsonExtractFunc(" in _line(root, "sqlite3.c", 218016)
    assert "SQLITE_PRIVATE void sqlite3WindowCodeStep(" in _line(root, "sqlite3.c", 180412)
    window = re.compile(r"(?<![-\w])over\s*[(\w]", re.I)
    for rel in ("test/select1.test", "test/tester.tcl"):
        text = (src / rel).read_text(errors="replace")
        assert "json" not in text.lower(), rel
        assert not window.search(text), rel
    # The testfixture build compiles a generated sqlite3.c that is the pinned amalgamation, byte for byte.
    assert coverage.sha256_file(bld / "sqlite3.c") == coverage.sha256_file(root / "sqlite3.c")


def test_sqlite_select1_runs_sqlite3Select_not_json_or_windows(sqlite):
    root, *_ = sqlite
    sel = _ask(root, "sqlite3Select@sqlite3.c")
    assert {t.test: t.level for t in sel.tests}.get("select1") == "exact"
    for name in ("jsonExtractFunc@sqlite3.c", "sqlite3WindowCodeStep@sqlite3.c"):
        res = _ask(root, name)
        assert "select1" not in {t.test for t in res.tests}, name
        assert [n.why.startswith("probable:") for n in res.no_test_reaches] == [True], name


def test_sqlite_recording_wrote_no_gcda_in_the_repo(sqlite):
    root, src, *_ = sqlite
    assert _gcda(root) == [] and _gcda(src) == []
