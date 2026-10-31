"""Command-line entry point."""

from __future__ import annotations

import argparse
import errno
import json
import socket
import sys
import threading
from typing import Callable, TextIO

from linuxpilot import __version__
from linuxpilot.context import Context
from linuxpilot.models import Status, worst_status
from linuxpilot.registry import all_checks, select_checks
from linuxpilot.render import render_human, render_json, to_json_obj, use_color, use_unicode
from linuxpilot.runner import RunResult, run_checks

# Exit codes (documented in the README). Usage errors use 64 (EX_USAGE) so
# they never collide with the "critical" health result.
EXIT_OK = 0
EXIT_WARNING = 1
EXIT_CRITICAL = 2
EXIT_USAGE = 64
EXIT_CANNOT_START = 70  # e.g. the dashboard port is already in use

DEFAULT_INTERVAL = 5.0
MIN_INTERVAL = 2.0
DEFAULT_PORT = 8765

EPILOG = """\
exit codes:
  0  everything checked is OK (or a live view was stopped with Ctrl+C)
  1  at least one WARNING
  2  at least one CRITICAL
  64 invalid command-line usage
  70 could not start (for example the dashboard port is in use)

examples:
  linuxpilot                      quick health summary
  linuxpilot status -v            include details and non-applicable checks
  linuxpilot status --only disk,memory
  linuxpilot status --json | jq '.findings[] | select(.status != "ok")'
  linuxpilot watch                live view in the terminal, Ctrl+C to stop
  linuxpilot serve                live dashboard in the browser, Ctrl+C to stop
  linuxpilot list-checks
"""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: error: {message}\n")


def _csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def _interval(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {value!r}") from None
    if seconds < MIN_INTERVAL:
        raise argparse.ArgumentTypeError(f"must be at least {MIN_INTERVAL:g} seconds")
    return seconds


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a port number: {value!r}") from None
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("must be between 1 and 65535")
    return port


def _add_common_args(p: argparse.ArgumentParser, suppress: bool) -> None:
    # Subcommands use SUPPRESS defaults so `linuxpilot --json status` and
    # `linuxpilot status --json` both work: a subparser default would
    # otherwise overwrite a flag given before the subcommand.
    d = {"default": argparse.SUPPRESS} if suppress else {}
    p.add_argument("--json", action="store_true", help="output machine-readable JSON", **d)
    p.add_argument("-v", "--verbose", action="store_true", help="show details and non-applicable checks", **d)
    p.add_argument("--no-color", action="store_true", help="disable colored output", **d)
    p.add_argument("--ascii", action="store_true", help="use ASCII symbols instead of Unicode", **d)


def _add_status_args(p: argparse.ArgumentParser, suppress: bool) -> None:
    d = {"default": argparse.SUPPRESS} if suppress else {}
    p.add_argument("--only", type=_csv, metavar="LIST", help="run only these categories or check ids (comma-separated)", **d)
    p.add_argument("--skip", type=_csv, metavar="LIST", help="skip these categories or check ids", **d)
    p.add_argument("--no-network", action="store_true", help="do not run checks that contact other hosts", **d)
    p.add_argument("--quick", action="store_true", help="skip checks that need a short sampling delay", **d)


def _add_interval_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--interval", type=_interval, default=DEFAULT_INTERVAL, metavar="SECONDS",
        help=f"seconds between refreshes (default {DEFAULT_INTERVAL:g}, minimum {MIN_INTERVAL:g})",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="linuxpilot",
        description="Lightweight, read-only health check for Linux servers and Raspberry Pi.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"linuxpilot {__version__}")
    _add_common_args(parser, suppress=False)
    # Allow `linuxpilot --only disk` without typing `status`.
    _add_status_args(parser, suppress=False)

    sub = parser.add_subparsers(dest="command", parser_class=_Parser)
    status = sub.add_parser(
        "status",
        help="check system health once (default command)",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_common_args(status, suppress=True)
    _add_status_args(status, suppress=True)

    watch = sub.add_parser(
        "watch",
        help="live, interactive view in the terminal; press q to quit",
        description="Re-run the checks every few seconds in an interactive view: move with the arrow "
        "keys, press Enter for details, f to filter, / to search, ? for all keys, q to quit. "
        "Nothing keeps running after you quit.",
    )
    _add_common_args(watch, suppress=True)
    _add_status_args(watch, suppress=True)
    _add_interval_arg(watch)
    watch.add_argument("--plain", action="store_true",
                       help="simple redrawing report without keyboard controls")

    serve = sub.add_parser(
        "serve",
        help="live dashboard in the browser; press Ctrl+C to stop",
        description="Start a small read-only web page that shows live results. It is only reachable "
        "from this computer unless you pass --allow-remote. It stops when you press Ctrl+C.",
        epilog="To view a server's dashboard from your laptop without opening any port:\n"
        "  ssh -L 8765:127.0.0.1:8765 user@server   then run `linuxpilot serve` there\n"
        "  and open http://127.0.0.1:8765 on your laptop.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_status_args(serve, suppress=True)
    _add_interval_arg(serve)
    serve.add_argument("--host", default="127.0.0.1",
                       help="address to listen on (default 127.0.0.1, only this computer)")
    serve.add_argument("--port", type=_port, default=DEFAULT_PORT, help=f"port to listen on (default {DEFAULT_PORT})")
    serve.add_argument("--allow-remote", action="store_true",
                       help="allow listening on a non-local address; access then needs a random token")
    serve.add_argument("--open", action="store_true", help="open the dashboard in your web browser")

    lc = sub.add_parser("list-checks", help="list available checks")
    _add_common_args(lc, suppress=True)
    return parser


def _exit_code(findings) -> int:
    overall = worst_status(findings)
    if overall is Status.CRITICAL:
        return EXIT_CRITICAL
    if overall is Status.WARNING:
        return EXIT_WARNING
    return EXIT_OK


def _validate_names(args: argparse.Namespace) -> bool:
    known = all_checks()
    names = {c.id for c in known} | {c.category for c in known}
    for name in (args.only or []) + (args.skip or []):
        if name not in names:
            sys.stderr.write(f"linuxpilot: unknown check or category '{name}' (see: linuxpilot list-checks)\n")
            return False
    return True


def _make_provider(args: argparse.Namespace) -> Callable[[], RunResult]:
    ctx = Context(network=not args.no_network, quick=args.quick)
    checks = select_checks(all_checks(), args.only, args.skip)
    return lambda: run_checks(ctx, checks)


def _human_renderer(args: argparse.Namespace, out: TextIO, tip: bool = False) -> Callable[[RunResult], str]:
    color = use_color(out, args.no_color)
    unicode = use_unicode(out) and not args.ascii
    return lambda r: render_human(r.findings, r.duration, color=color, unicode=unicode, verbose=args.verbose, tip=tip)


def _primary_ip() -> str | None:
    """This machine's main LAN address, for printing a usable link.

    Connecting a UDP socket only picks a route; no packet is sent.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # TEST-NET-1, never routed on the internet
            return s.getsockname()[0]
    except OSError:
        return None


def _stop_on_terminate() -> None:
    """Treat `kill` (SIGTERM) like Ctrl+C so live views always shut down cleanly."""
    import signal

    def handler(signum, frame):  # noqa: ARG001
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, handler)


def cmd_list_checks(args: argparse.Namespace, out: TextIO) -> int:
    checks = all_checks()
    if args.json:
        out.write(
            json.dumps(
                [
                    {"id": c.id, "category": c.category, "title": c.title, "network": c.network, "description": c.description}
                    for c in checks
                ],
                indent=2,
            )
            + "\n"
        )
        return EXIT_OK
    width = max(len(c.id) for c in checks)
    for c in checks:
        net = " (network)" if c.network else ""
        out.write(f"{c.id.ljust(width)}  {c.description}{net}\n")
    out.write("\nUse --only / --skip with a category (e.g. disk) or a full id.\n")
    return EXIT_OK


class _Spinner:
    """A small 'Checking...' animation on stderr, only on a real terminal."""

    FRAMES_UNICODE = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    FRAMES_ASCII = "-\\|/"

    def __init__(self, stream: TextIO, unicode: bool, enabled: bool) -> None:
        self.stream = stream
        self.frames = self.FRAMES_UNICODE if unicode else self.FRAMES_ASCII
        self.enabled = enabled
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _spin(self) -> None:
        i = 0
        # Wait a moment first so very fast runs do not flash.
        if self._stop.wait(0.15):
            return
        while not self._stop.is_set():
            self.stream.write(f"\r{self.frames[i % len(self.frames)]} Checking this machine...")
            self.stream.flush()
            i += 1
            self._stop.wait(0.08)

    def __enter__(self) -> "_Spinner":
        if self.enabled:
            self._thread = threading.Thread(target=self._spin, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        if self._thread is not None:
            self._stop.set()
            self._thread.join()
            self.stream.write("\r\033[K")
            self.stream.flush()


def cmd_status(args: argparse.Namespace, out: TextIO) -> int:
    if not _validate_names(args):
        return EXIT_USAGE
    interactive = not args.json and hasattr(out, "isatty") and out.isatty() and sys.stderr.isatty()
    with _Spinner(sys.stderr, use_unicode(sys.stderr) and not args.ascii, enabled=interactive):
        result = _make_provider(args)()
    if args.json:
        out.write(render_json(result.findings, result.duration))
    else:
        out.write(_human_renderer(args, out, tip=interactive)(result))
    return _exit_code(result.findings)


def cmd_watch(args: argparse.Namespace, out: TextIO) -> int:
    from linuxpilot.live import watch

    if not _validate_names(args):
        return EXIT_USAGE
    on_terminal = hasattr(out, "isatty") and out.isatty() and sys.stdin.isatty()
    if on_terminal and not args.json and not args.plain:
        import termios

        from linuxpilot import tui

        _stop_on_terminate()
        try:
            tui.run(_make_provider(args), args.interval,
                    color=use_color(out, args.no_color), unicode=use_unicode(out) and not args.ascii, out=out)
            return EXIT_OK
        except KeyboardInterrupt:
            return EXIT_OK
        except termios.error:
            pass  # not a real terminal after all; fall back to the plain view

    if args.json:
        # One compact JSON object per line: easy to pipe into jq or a log file.
        def render(r: RunResult) -> str:
            return json.dumps(to_json_obj(r.findings, r.duration)) + "\n"
        interactive = False
    else:
        render = _human_renderer(args, out)
        interactive = hasattr(out, "isatty") and out.isatty()
    _stop_on_terminate()
    try:
        watch(_make_provider(args), render, out, args.interval, interactive=interactive)
    except KeyboardInterrupt:
        sys.stderr.write("Stopped.\n")
    return EXIT_OK


def cmd_serve(args: argparse.Namespace, out: TextIO) -> int:
    from linuxpilot.dashboard import is_loopback, make_server, new_token

    if not _validate_names(args):
        return EXIT_USAGE
    loopback = is_loopback(args.host)
    if not loopback and not args.allow_remote:
        sys.stderr.write(
            f"linuxpilot: refusing to listen on {args.host}, which other computers can reach.\n"
            "The safer way to view a remote machine is an SSH tunnel:\n"
            f"  ssh -L {args.port}:127.0.0.1:{args.port} user@this-machine\n"
            "If you really want this, add --allow-remote (a random access token will be required).\n"
        )
        return EXIT_USAGE

    token = None if loopback else new_token()
    try:
        server = make_server(args.host, args.port, _make_provider(args), args.interval, token=token)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            sys.stderr.write(f"linuxpilot: port {args.port} is already in use. Try another one, "
                             f"for example: linuxpilot serve --port {args.port + 1}\n")
        else:
            sys.stderr.write(f"linuxpilot: could not start the dashboard on {args.host}:{args.port}: {exc}\n")
        return EXIT_CANNOT_START

    shown_host = "127.0.0.1" if loopback and args.host in ("127.0.0.1", "localhost") else args.host
    if shown_host in ("0.0.0.0", "::", "[::]"):
        shown_host = _primary_ip() or socket.gethostname()
    if ":" in shown_host:
        shown_host = f"[{shown_host}]"
    url = f"http://{shown_host}:{server.server_address[1]}/"
    if token:
        url += f"#token={token}"

    lines = [
        "LinuxPilot live dashboard",
        f"  Open:  {url}",
        f"  Refreshes every {args.interval:g}s while this command runs. Press Ctrl+C to stop.",
    ]
    if loopback:
        lines.append("  Only this computer can open it. To view it from another computer, use an SSH tunnel:")
        lines.append(f"    ssh -L {server.server_address[1]}:127.0.0.1:{server.server_address[1]} user@this-machine")
    else:
        lines.append("  Anyone who can reach this address and has the full link (with #token=...) can view it.")
        lines.append("  Traffic is not encrypted. Prefer an SSH tunnel or a VPN such as Tailscale or WireGuard.")
    out.write("\n".join(lines) + "\n")
    out.flush()

    if args.open:
        try:
            import webbrowser

            webbrowser.open(url)
        except Exception:  # no browser on a headless server is fine
            pass

    _stop_on_terminate()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        sys.stderr.write("\nStopped.\n")
    finally:
        server.server_close()
    return EXIT_OK


def main(argv: list[str] | None = None, out: TextIO | None = None) -> int:
    out = out or sys.stdout
    if not sys.platform.startswith("linux"):
        sys.stderr.write("linuxpilot: this tool only supports Linux.\n")
        return EXIT_USAGE
    args = build_parser().parse_args(argv)
    commands = {
        "list-checks": cmd_list_checks,
        "watch": cmd_watch,
        "serve": cmd_serve,
    }
    try:
        return commands.get(args.command, cmd_status)(args, out)
    except KeyboardInterrupt:
        sys.stderr.write("\ninterrupted\n")
        return 130
    except BrokenPipeError:  # e.g. `linuxpilot | head`
        return EXIT_OK
