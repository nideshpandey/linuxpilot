"""`linuxpilot serve`: a live dashboard in the browser, only while the command runs.

Design rules:
- Binds to 127.0.0.1 by default. Other addresses need --allow-remote, which
  also requires a random access token on every data request.
- Read-only: the only data endpoint is GET /api/status.
- Self-contained page: no CDN, fonts or external requests, works offline.
- Checks run at most once per refresh interval, however many tabs are open.
- Stops completely on Ctrl+C. Nothing is installed or left running.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import secrets
import socket
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlsplit

from linuxpilot.dashboard_assets import APP_CSS, APP_JS, INDEX_HTML
from linuxpilot.render import to_json_obj
from linuxpilot.runner import RunResult

TOKEN_HEADER = "X-LinuxPilot-Token"
SNAPSHOT_MIN_AGE = 1.5  # seconds

_SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
}

_STATIC = {
    "/": ("text/html; charset=utf-8", INDEX_HTML),
    "/index.html": ("text/html; charset=utf-8", INDEX_HTML),
    "/app.css": ("text/css; charset=utf-8", APP_CSS),
    "/app.js": ("text/javascript; charset=utf-8", APP_JS),
}


def is_loopback(host: str) -> bool:
    host = host.strip("[]")
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _host_without_port(header: str) -> str:
    header = header.strip()
    if header.startswith("["):  # [::1]:8765
        return header[1:].split("]", 1)[0]
    if header.count(":") == 1:  # 127.0.0.1:8765 or localhost:8765
        return header.split(":", 1)[0]
    return header


class Snapshot:
    """Caches the latest results so many viewers cause one check run per interval."""

    def __init__(self, provider: Callable[[], RunResult], max_age: float, interval: float) -> None:
        self._provider = provider
        self._max_age = max_age
        self._interval = interval
        self._lock = threading.Lock()
        self._data: dict | None = None
        self._taken = 0.0

    def get(self) -> dict:
        with self._lock:
            now = time.monotonic()
            if self._data is None or now - self._taken >= self._max_age:
                result = self._provider()
                data = to_json_obj(result.findings, result.duration)
                data["checked_at"] = time.time()
                data["interval_seconds"] = self._interval
                self._data = data
                self._taken = time.monotonic()
            return self._data


def make_handler(snapshot: Snapshot, token: str | None, check_host: bool) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "LinuxPilot"
        sys_version = ""
        # HTTP/1.0: close the connection after each response, so no browser
        # tab can keep a handler thread alive after the server is stopped.
        protocol_version = "HTTP/1.0"

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            pass  # stay quiet; the terminal shows how to stop the server instead

        def _send(self, status: int, content_type: str, body: bytes, head_only: bool = False) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for k, v in _SECURITY_HEADERS.items():
                self.send_header(k, v)
            self.end_headers()
            if not head_only:
                self.wfile.write(body)

        def _json(self, status: int, obj: dict, head_only: bool = False) -> None:
            self._send(status, "application/json; charset=utf-8", json.dumps(obj).encode(), head_only)

        def _authorised(self, query: str) -> bool:
            if token is None:
                return True
            supplied = self.headers.get(TOKEN_HEADER) or (parse_qs(query).get("token") or [""])[0]
            return hmac.compare_digest(supplied.encode(), token.encode())

        def _handle(self, head_only: bool) -> None:
            # Block DNS-rebinding: a page on another site must not be able to
            # read this dashboard by pointing its own hostname at 127.0.0.1.
            if check_host and not is_loopback(_host_without_port(self.headers.get("Host", ""))):
                self._json(HTTPStatus.FORBIDDEN, {"error": "Use http://127.0.0.1 or http://localhost to open this dashboard."}, head_only)
                return
            parts = urlsplit(self.path)
            if parts.path in _STATIC:
                ctype, body = _STATIC[parts.path]
                self._send(HTTPStatus.OK, ctype, body.encode("utf-8"), head_only)
                return
            if parts.path == "/api/status":
                if not self._authorised(parts.query):
                    self._json(HTTPStatus.UNAUTHORIZED, {"error": "missing or wrong access token"}, head_only)
                    return
                try:
                    data = snapshot.get()
                except Exception as exc:  # report, keep serving
                    self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"{type(exc).__name__}: {exc}"}, head_only)
                    return
                self._json(HTTPStatus.OK, data, head_only)
                return
            if parts.path == "/favicon.ico":
                self._send(HTTPStatus.NO_CONTENT, "image/x-icon", b"", head_only)
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"}, head_only)

        def do_GET(self) -> None:  # noqa: N802
            self._handle(head_only=False)

        def do_HEAD(self) -> None:  # noqa: N802
            self._handle(head_only=True)

        def _not_allowed(self) -> None:
            self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
            self.send_header("Allow", "GET, HEAD")
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_POST = do_PUT = do_DELETE = do_PATCH = _not_allowed  # noqa: N815

    return Handler


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class _Server6(_Server):
    address_family = socket.AF_INET6


def make_server(
    host: str,
    port: int,
    provider: Callable[[], RunResult],
    interval: float,
    *,
    token: str | None = None,
) -> ThreadingHTTPServer:
    """Create (but do not start) the dashboard server."""
    loopback = is_loopback(host)
    # The page chooses its own refresh rate; checks still run at most once
    # every SNAPSHOT_MIN_AGE seconds however many tabs are polling.
    snapshot = Snapshot(provider, max_age=SNAPSHOT_MIN_AGE, interval=interval)
    handler = make_handler(snapshot, token, check_host=loopback)
    cls = _Server6 if ":" in host.strip("[]") else _Server
    return cls((host.strip("[]"), port), handler)


def new_token() -> str:
    return secrets.token_urlsafe(24)
