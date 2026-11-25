"""The dashboard ships in the wheel and works from a fresh install, with nothing else on disk.

test_wheel_has_the_dashboard: builds the wheel and checks its files (fast, no network).
test_fresh_install_serves_the_dashboard: installs that wheel into a new virtual environment (needs the
network once, for the dependencies) and serves `aerotrace ui` from it. Marked realrepo, so CI runs it
on every PR on Linux and macOS.
"""

from __future__ import annotations

import http.client
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
WEB = [
    "app.html",
    "app.js",
    "app.css",
    "graph.js",
    "tokens.css",
    "bench-page.js",
    "bench-page.css",
    "benchmark.json",
]
FONTS = ["Inter-Regular.woff2", "Inter-Medium.woff2", "Inter-SemiBold.woff2", "JetBrainsMono-Regular.woff2",
         "JetBrainsMono-Medium.woff2", "JetBrainsMono-SemiBold.woff2", "Inter-LICENSE.txt", "JetBrainsMono-OFL.txt"]  # fmt: skip


@pytest.fixture(scope="module")
def wheel(tmp_path_factory) -> Path:
    if not shutil.which("uv"):
        pytest.skip("uv not found")
    out = tmp_path_factory.mktemp("dist")
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(out)], cwd=ROOT, check=True, capture_output=True
    )
    return next(out.glob("aerotrace-*.whl"))


def test_wheel_has_the_dashboard(wheel: Path) -> None:
    names = set(zipfile.ZipFile(wheel).namelist())
    for f in WEB:
        assert f"aerotrace/ui/web/{f}" in names, f
    for f in FONTS:
        assert f"aerotrace/ui/web/fonts/{f}" in names, f
    assert not any(n.startswith(("bench/", "tests/", "docs/")) for n in names)  # dev-only things stay out


@pytest.mark.realrepo
def test_fresh_install_serves_the_dashboard(wheel: Path, tmp_path: Path) -> None:
    venv = tmp_path / "venv"
    subprocess.run(["uv", "venv", "--python", sys.executable, str(venv)], check=True, capture_output=True)
    subprocess.run(
        ["uv", "pip", "install", "--python", str(venv), str(wheel)], check=True, capture_output=True
    )
    exe = venv / ("Scripts" if sys.platform == "win32" else "bin") / "aerotrace"
    repo = tmp_path / "repo"
    repo.mkdir()
    p = subprocess.Popen(
        [str(exe), "ui", "--root", str(repo), "--no-open"], stdout=subprocess.PIPE, text=True
    )
    try:
        line = ""
        deadline = time.time() + 30
        while "http://" not in line and time.time() < deadline:
            line = p.stdout.readline()
        url = line[line.index("http://") :].split()[0]
        host, rest = url.removeprefix("http://").split("/", 1)
        c = http.client.HTTPConnection(*host.split(":"), timeout=10)
        c.request("GET", "/" + rest, headers={"Host": host})
        r = c.getresponse()
        r.read()
        assert r.status == 303
        cookie = r.getheader("Set-Cookie").split(";")[0]
        for path, want in (("/", b"/static/app.js"), ("/static/graph.js", b"AeroTraceGraph"),
                           ("/api/overview", b'"status":"no_index"'), ("/api/benchmark", b'"headline"')):  # fmt: skip
            c.request("GET", path, headers={"Host": host, "Cookie": cookie})
            r = c.getresponse()
            body = r.read()
            assert r.status == 200 and want in body, path
    finally:
        p.terminate()
        p.wait(10)
