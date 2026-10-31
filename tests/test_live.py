"""Tests for `linuxpilot watch` and `linuxpilot serve`."""

import io
import json
import threading
import urllib.error
import urllib.request

import pytest

from linuxpilot.cli import EXIT_OK, EXIT_USAGE, build_parser, main
from linuxpilot.dashboard import TOKEN_HEADER, Snapshot, is_loopback, make_server
from linuxpilot.live import watch
from linuxpilot.models import Finding, Status
from linuxpilot.runner import RunResult


def sample(status=Status.OK):
    return RunResult([Finding("x.y", "Thing", status, "summary", reason="why", recommendation="do `this`")], 0.1)


# ------------------------------------------------------------------ watch


def test_watch_appends_when_not_a_terminal():
    out, sleeps = io.StringIO(), []
    watch(sample, lambda r: "frame\n", out, 3, interactive=False, sleep=sleeps.append, max_cycles=3)
    assert out.getvalue() == "frame\n" * 3
    assert sleeps == [3, 3]  # no sleep after the last cycle
    assert "\033[" not in out.getvalue()


def test_watch_redraws_in_place_on_a_terminal_and_restores_screen():
    out = io.StringIO()
    watch(sample, lambda r: "frame\n", out, 2, interactive=True, sleep=lambda s: None, max_cycles=2)
    text = out.getvalue()
    assert text.startswith("\033[?1049h")  # alternate screen, like `top`
    assert text.count("\033[H\033[J") == 2
    assert "Press Ctrl+C to stop" in text
    assert text.endswith("\033[?25h\033[?1049l")  # cursor back, screen restored


def test_watch_restores_screen_when_interrupted():
    out = io.StringIO()

    def stop(_):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        watch(sample, lambda r: "frame\n", out, 2, interactive=True, sleep=stop)
    assert out.getvalue().endswith("\033[?1049l")


def test_watch_interval_has_a_minimum(capsys):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["watch", "--interval", "0.5"])
    assert exc.value.code == EXIT_USAGE


def test_watch_command_stops_cleanly_on_ctrl_c(monkeypatch):
    import linuxpilot.live as live

    def fake_watch(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(live, "watch", fake_watch)
    assert main(["watch", "--quick", "--no-network"], out=io.StringIO()) == EXIT_OK


# ------------------------------------------------------------------ serve: helpers


def test_is_loopback():
    for host in ("127.0.0.1", "127.8.9.1", "::1", "[::1]", "localhost"):
        assert is_loopback(host), host
    for host in ("0.0.0.0", "192.168.1.5", "::", "example.com", ""):
        assert not is_loopback(host), host


def test_snapshot_runs_checks_once_per_interval():
    calls = []

    def provider():
        calls.append(1)
        return sample()

    snap = Snapshot(provider, max_age=60, interval=5)
    first, second = snap.get(), snap.get()
    assert len(calls) == 1 and first is second
    assert first["interval_seconds"] == 5 and "checked_at" in first


# ------------------------------------------------------------------ serve: HTTP


@pytest.fixture
def server_factory():
    servers = []

    def start(token=None, host="127.0.0.1", provider=sample):
        srv = make_server(host, 0, provider, interval=2, token=token)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}"

    yield start
    for srv in servers:
        srv.shutdown()
        srv.server_close()


def fetch(url, headers=None, method="GET"):
    req = urllib.request.Request(url, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as res:
            return res.status, dict(res.headers), res.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers), err.read()


def test_serves_page_assets_and_status(server_factory):
    base = server_factory()
    status, headers, body = fetch(base + "/")
    assert status == 200 and b"LinuxPilot" in body
    assert "default-src 'none'" in headers["Content-Security-Policy"]
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Cache-Control"] == "no-store"
    for asset in ("/app.js", "/app.css"):
        assert fetch(base + asset)[0] == 200

    status, _, body = fetch(base + "/api/status")
    data = json.loads(body)
    assert status == 200
    assert data["overall_status"] == "ok"
    assert data["findings"][0]["check_id"] == "x.y"


def test_page_has_no_external_resources(server_factory):
    base = server_factory()
    for path in ("/", "/app.js", "/app.css"):
        # The SVG namespace is an identifier string, not a request.
        body = fetch(base + path)[2].decode().replace("http://www.w3.org/2000/svg", "")
        assert "http://" not in body and "https://" not in body, path
    # System values must be inserted as text, never parsed as HTML.
    assert "innerHTML" not in fetch(base + "/app.js")[2].decode()


def test_rejects_foreign_host_header(server_factory):
    base = server_factory()
    status, _, _ = fetch(base + "/api/status", headers={"Host": "attacker.example"})
    assert status == 403
    assert fetch(base + "/api/status", headers={"Host": "localhost:9999"})[0] == 200


def test_read_only_methods(server_factory):
    base = server_factory()
    assert fetch(base + "/api/status", method="POST")[0] == 405
    assert fetch(base + "/api/status", method="DELETE")[0] == 405
    assert fetch(base + "/nope")[0] == 404


def test_token_required_when_set(server_factory):
    base = server_factory(token="s3cret")
    assert fetch(base + "/api/status")[0] == 401
    assert fetch(base + "/api/status", headers={TOKEN_HEADER: "wrong"})[0] == 401
    assert fetch(base + "/api/status", headers={TOKEN_HEADER: "s3cret"})[0] == 200
    assert fetch(base + "/api/status?token=s3cret")[0] == 200
    # The page itself holds no data, so it loads without the token.
    assert fetch(base + "/")[0] == 200


def test_provider_crash_is_reported_not_fatal(server_factory):
    def broken():
        raise RuntimeError("boom")

    base = server_factory(provider=broken)
    status, _, body = fetch(base + "/api/status")
    assert status == 500 and b"boom" in body
    assert fetch(base + "/")[0] == 200  # still serving


def test_serve_refuses_public_address_without_flag(capsys):
    assert main(["serve", "--host", "0.0.0.0"]) == EXIT_USAGE
    assert "ssh -L" in capsys.readouterr().err


def test_serve_validates_port():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["serve", "--port", "70000"])
