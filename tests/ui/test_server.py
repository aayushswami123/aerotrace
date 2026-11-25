"""The dashboard server: local only, read-only, token-gated, strict headers."""

from __future__ import annotations

import http.client
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from aerotrace.ui import server


@pytest.fixture
def srv(tmp_path: Path) -> Iterator[server.DashboardServer]:
    s = server.DashboardServer(tmp_path)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield s
    s.shutdown()
    s.server_close()


def _req(srv: server.DashboardServer, path: str, method: str = "GET", headers: dict | None = None):
    port = srv.server_address[1]
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    h = {"Host": f"127.0.0.1:{port}", **(headers or {})}
    c.request(method, path, headers=h)
    r = c.getresponse()
    body = r.read()
    c.close()
    return r, body


def _cookie(srv: server.DashboardServer) -> dict:
    return {"Cookie": f"{srv.cookie}={srv.token}"}


def test_listens_on_loopback_only(srv: server.DashboardServer) -> None:
    assert srv.server_address[0] == "127.0.0.1"


def test_token_link_sets_a_strict_cookie_and_cleans_the_url(srv: server.DashboardServer) -> None:
    r, _ = _req(srv, f"/?t={srv.token}")
    assert r.status == 303 and r.getheader("Location") == "/"
    cookie = r.getheader("Set-Cookie")
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie


def test_everything_needs_the_cookie(srv: server.DashboardServer) -> None:
    for path in ("/", "/api/overview", "/static/app.js", "/?t=wrong"):
        r, _ = _req(srv, path)
        assert r.status == 403, path
    r, _ = _req(srv, "/api/overview", headers={"Cookie": f"{srv.cookie}=nope"})
    assert r.status == 403


def test_with_the_cookie_pages_and_api_answer(srv: server.DashboardServer) -> None:
    r, body = _req(srv, "/", headers=_cookie(srv))
    assert r.status == 200 and b"/static/app.js" in body
    r, body = _req(srv, "/api/overview", headers=_cookie(srv))
    assert r.status == 200 and b'"status":"no_index"' in body
    r, _ = _req(srv, "/static/tokens.css", headers=_cookie(srv))
    assert r.status == 200


def test_host_and_origin_are_checked(srv: server.DashboardServer) -> None:
    r, _ = _req(srv, "/api/overview", headers={**_cookie(srv), "Host": "evil.example:80"})
    assert r.status == 403  # DNS rebinding
    port = srv.server_address[1]
    r, _ = _req(srv, "/api/overview", headers={**_cookie(srv), "Origin": "http://evil.example"})
    assert r.status == 403
    r, _ = _req(srv, "/api/overview", headers={**_cookie(srv), "Sec-Fetch-Site": "cross-site"})
    assert r.status == 403
    r, _ = _req(srv, "/api/overview", headers={**_cookie(srv), "Origin": f"http://127.0.0.1:{port}"})
    assert r.status == 200


def test_read_only(srv: server.DashboardServer) -> None:
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        r, _ = _req(srv, "/api/overview", method=method, headers=_cookie(srv))
        assert r.status == 405, method


def test_no_file_outside_the_web_folder(srv: server.DashboardServer) -> None:
    for path in ("/static/../api.py", "/static/..%2F..%2Fapi.py", "/static/%2e%2e/server.py", "/static//etc/passwd",
                 "/api/checks/..%2F..%2Findex.db", "/api/checks/../../index"):  # fmt: skip
        r, _ = _req(srv, path, headers=_cookie(srv))
        assert r.status == 404, path


def test_strict_headers(srv: server.DashboardServer) -> None:
    r, _ = _req(srv, "/", headers=_cookie(srv))
    csp = r.getheader("Content-Security-Policy")
    assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp and "unsafe-inline" not in csp
    assert r.getheader("X-Content-Type-Options") == "nosniff"
    assert r.getheader("Cache-Control") == "no-store"
    assert r.getheader("Access-Control-Allow-Origin") is None


def test_served_files_are_only_from_the_package() -> None:
    files = server._web_files()
    assert "app.html" in files and "app.js" in files and "tokens.css" in files
    assert any(name.startswith("fonts/") for name in files)
    assert all(not name.endswith(".py") for name in files)


def test_the_token_is_in_no_answer(srv: server.DashboardServer) -> None:
    for path in ("/", "/api/overview", "/api/checks", "/api/runinfo", "/static/app.js"):
        r, body = _req(srv, path, headers=_cookie(srv))
        assert srv.token.encode() not in body, path
        assert all(srv.token not in v for k, v in r.getheaders() if k.lower() != "set-cookie"), path


def test_each_dashboard_has_its_own_cookie(tmp_path: Path) -> None:
    a, b = server.DashboardServer(tmp_path), server.DashboardServer(tmp_path)
    try:
        assert a.cookie != b.cookie and str(a.server_address[1]) in a.cookie
    finally:
        a.server_close()
        b.server_close()


def test_head_and_builtin_errors_get_the_headers(srv: server.DashboardServer) -> None:
    r, body = _req(srv, "/api/overview", method="HEAD", headers=_cookie(srv))
    assert r.status == 200 and body == b""
    r, _ = _req(srv, "/", method="TRACE", headers=_cookie(srv))
    assert r.status == 501 and "default-src 'none'" in r.getheader("Content-Security-Policy", "")
