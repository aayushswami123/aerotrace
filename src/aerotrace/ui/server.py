"""`aerotrace ui`: a read-only dashboard on 127.0.0.1. A thin layer: it maps URLs to `ui/api.py`.

Security (docs/plans/dashboard-and-benchmark.md section 6):
- listens on 127.0.0.1 only and answers only GET and HEAD;
- a random token is in the first URL; the server swaps it for an HttpOnly, SameSite=Strict cookie and
  redirects to a clean URL; every other request needs that cookie (compared in constant time);
- the Host header must be this server, and Origin / Sec-Fetch-Site must say same-origin;
- static files come only from `ui/web/` (no path from the URL is joined into a file path);
- a strict Content-Security-Policy; no CORS headers; API answers are not cached.
It writes nothing and runs nothing.
"""

from __future__ import annotations

import hmac
import json
import mimetypes
import secrets
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from aerotrace.ui import api

COOKIE = "aerotrace_ui"  # the port is added, so two dashboards on one machine keep separate cookies
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; font-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)
WEB_TYPES = {".html", ".css", ".js", ".woff2", ".svg"}


def _web_files() -> dict[str, tuple[bytes, str]]:
    """Every file the dashboard may serve: name -> (bytes, content type). Read once."""
    web = resources.files("aerotrace.ui") / "web"
    out = {}

    def walk(d, prefix: str) -> None:
        for e in d.iterdir():
            name = prefix + e.name
            if e.is_dir():
                walk(e, name + "/")
            elif Path(e.name).suffix in WEB_TYPES:
                ctype = mimetypes.guess_type(e.name)[0] or "application/octet-stream"
                if e.name.endswith(".woff2"):
                    ctype = "font/woff2"
                out[name] = (e.read_bytes(), ctype)

    walk(web, "")
    return out


class Handler(BaseHTTPRequestHandler):
    server: DashboardServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:  # quiet: no access log
        return

    # ---- helpers

    def _send(
        self, status: int, body: bytes, ctype: str, extra: dict[str, str] | None = None, csp: str = CSP
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", csp)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, data: object) -> None:
        body = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    def _host_ok(self) -> bool:
        port = self.server.server_address[1]
        return self.headers.get("Host", "") in {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        port = self.server.server_address[1]
        if origin is not None and origin not in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}:
            return False
        return self.headers.get("Sec-Fetch-Site", "same-origin") in {"same-origin", "none"}

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        # the standard library's own error answers (bad request lines, unknown methods) get the same headers
        self.close_connection = True
        self._error(code, message or HTTPStatus(code).phrase)

    def _cookie_ok(self) -> bool:
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == self.server.cookie and hmac.compare_digest(value.encode(), self.server.token.encode()):
                return True
        return False

    # ---- methods

    def do_POST(self) -> None:
        self._error(HTTPStatus.METHOD_NOT_ALLOWED, "This dashboard is read-only.")

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_POST

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        if not self._host_ok() or not self._origin_ok():
            self._error(HTTPStatus.FORBIDDEN, "Requests must come from the dashboard itself.")
            return
        url = urlsplit(self.path)
        if url.path == "/" and "t" in parse_qs(url.query):
            given = parse_qs(url.query)["t"][0]
            if hmac.compare_digest(given.encode(), self.server.token.encode()):
                cookie = f"{self.server.cookie}={self.server.token}; HttpOnly; SameSite=Strict; Path=/"
                self._send(HTTPStatus.SEE_OTHER, b"", "text/plain", {"Location": "/", "Set-Cookie": cookie})
                return
        if not self._cookie_ok():
            self._error(HTTPStatus.FORBIDDEN, "Open the dashboard from the link `aerotrace ui` printed.")
            return
        try:
            self._route(unquote(url.path))
        except Exception as e:  # an answer, never a crash; the reason is shown on the page
            print(f"aerotrace ui: {type(e).__name__} answering {self.path.split('?')[0]}", file=sys.stderr)
            self._error(
                HTTPStatus.INTERNAL_SERVER_ERROR, f"The dashboard hit an unexpected {type(e).__name__}."
            )

    def _route(self, path: str) -> None:
        root = self.server.root
        if path == "/favicon.ico":
            self._send(HTTPStatus.NO_CONTENT, b"", "image/x-icon")
        elif path in ("/", "/index.html"):
            body, ctype = self.server.files["app.html"]
            self._send(HTTPStatus.OK, body, ctype)
        elif path.startswith("/static/"):
            f = self.server.files.get(path.removeprefix("/static/"))
            if f is None:
                self._error(HTTPStatus.NOT_FOUND, "No such file.")
            else:
                self._send(HTTPStatus.OK, *f)
        elif path == "/api/overview":
            self._json(HTTPStatus.OK, api.overview(root))
        elif path == "/api/checks":
            self._json(HTTPStatus.OK, api.checks(root))
        elif path.startswith("/api/graph/"):
            try:
                self._json(HTTPStatus.OK, api.graph(root, path.removeprefix("/api/graph/")))
            except api.NotFound:
                self._error(HTTPStatus.NOT_FOUND, "No saved check with that id.")
            except api.Unreadable as e:
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, str(e))
        elif path == "/api/node":
            q = parse_qs(urlsplit(self.path).query)
            try:
                self._json(HTTPStatus.OK, api.node(root, q.get("check", [""])[0], q.get("node", [""])[0]))
            except api.NotFound:
                self._error(HTTPStatus.NOT_FOUND, "No such node in that check.")
            except api.Unreadable as e:
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, str(e))
        elif path.startswith("/api/checks/"):
            try:
                self._json(HTTPStatus.OK, api.check(root, path.removeprefix("/api/checks/")))
            except api.NotFound:
                self._error(HTTPStatus.NOT_FOUND, "No saved check with that id.")
            except api.Unreadable as e:
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, str(e))
        elif path == "/api/benchmark":
            self._json(HTTPStatus.OK, {"benchmark": api.benchmark()})
        elif path == "/benchmark-report":
            data = api.benchmark()
            if data is None:
                self._error(HTTPStatus.NOT_FOUND, "This AeroTrace has no benchmark results.")
            else:
                from aerotrace.ui import bench_page

                html = bench_page.render(data)
                # the report carries its own hashed policy; send exactly that, never a looser one
                page_csp = bench_page.csp_of(html) + "; frame-ancestors 'none'"
                self._send(HTTPStatus.OK, html.encode("utf-8"), "text/html; charset=utf-8", csp=page_csp)
                return
        elif path == "/api/runinfo":
            self._json(HTTPStatus.OK, api.runinfo(root))
        else:
            self._error(HTTPStatus.NOT_FOUND, "No such page.")


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, root: Path, port: int = 0):
        super().__init__(("127.0.0.1", port), Handler)
        self.root = root
        self.token = secrets.token_urlsafe(32)
        self.cookie = f"{COOKIE}_{self.server_address[1]}"
        self.files = _web_files()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/?t={self.token}"


def serve(root: Path, port: int = 0, open_browser: bool = True) -> int:
    srv = DashboardServer(root, port)
    print(f"AeroTrace dashboard for {api.repo_name(root)}: {srv.url}", flush=True)
    print(
        "Read-only and local: it changes nothing and loads nothing from the internet. Ctrl+C stops it.",
        flush=True,
    )
    if open_browser:
        threading.Timer(0.3, lambda: webbrowser.open(srv.url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.", file=sys.stderr)
    finally:
        srv.server_close()
    return 0
