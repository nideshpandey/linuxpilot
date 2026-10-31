# Contributing to LinuxPilot

Thanks for helping! LinuxPilot stays **lightweight, read-only, dependency-free and honest
about what it can't measure**. Please keep these principles in mind:

- **Read-only by default.** Checks never change the system. Anything that would change it
  must be explicit, previewed and confirmed.
- **Lightweight.** Standard library only, no background service, no database. The basic
  check must stay fast and must not load the dashboard or the interactive view.
- **Explain findings.** Say what was found, why it matters, and a safe next step.
- **Honest.** If something can't be measured, report `unknown` or `unsupported` with a note.
  Never guess, and never treat a missing value as zero.
- **Don't assume.** Not every machine has systemd, sensors, root access or a network.
- **Private.** Nothing is sent anywhere. The dashboard listens on 127.0.0.1 unless the user
  deliberately chooses otherwise.
- **Small, tested changes.** Add tests for the normal case, the problem case, and missing or
  unreadable data.

## Project layout

```text
src/linuxpilot/
  cli.py          argument parsing, exit codes
  context.py      ALL system access (files, statvfs, commands) goes through Context
  models.py       Finding and Status
  registry.py     @check decorator
  runner.py       runs checks in parallel; crashes become UNKNOWN findings
  render.py       human and JSON output (no system access)
  tui.py          interactive `linuxpilot watch` (termios + ANSI; View holds state, no I/O)
  live.py         plain `linuxpilot watch --plain` loop, also used when output is not a terminal
  dashboard.py    `linuxpilot serve` HTTP server: loopback by default, read-only, token for remote
  dashboard_assets.py  the dashboard's HTML, CSS and JS (self-contained, no external requests)
  checks/         one module per area: system, cpu, memory, disk, services, network, pi
tests/            fixture-based tests; never depend on the developer's machine
```

## Adding a check

1. Pick a module in `src/linuxpilot/checks/` (or create one and import it in `checks/__init__.py`).
2. Write a function that takes a `Context` and returns a `Finding` (or a list of them):

```python
from linuxpilot.context import Context
from linuxpilot.models import Finding, Status
from linuxpilot.registry import check


@check("area.thing", "Thing")
def thing(ctx: Context) -> Finding:
    """One-line description shown by `linuxpilot list-checks`."""
    text = ctx.read_or_none("/proc/something")
    if text is None:
        return Finding("area.thing", "Thing", Status.UNKNOWN, "could not read /proc/something",
                       limitations=["/proc/something unreadable"])
    ...
    return Finding(
        "area.thing", "Thing", Status.WARNING, "short, specific summary",
        details={"measured_value": 42},
        reason="Why this matters, in plain language.",
        recommendation="A safe, read-only next step, with the exact command.",
        source="/proc/something",
    )
```

### Rules

- **Use `ctx`** for every file read, `statvfs`, and command. Never `open()` or `subprocess` directly.
- **Commands:** explicit argument lists only (`ctx.run(["systemctl", "show", unit])`), never a shell string.
- **Never invent values.** If data is missing, return `UNKNOWN` (couldn't inspect) or `UNSUPPORTED`
  (doesn't apply), with a `limitations` note. A missing value is never zero.
- **Be conservative.** A warning should be something a person would want to act on.
  Noisy checks train people to ignore the tool.
- **Network traffic** only in checks registered with `network=True`.
- **Recommendations must be safe.** Suggest commands that inspect; if a fix changes the system,
  say what it does.
- Standard library only.

## Tests

Each check needs tests for the normal case, the warning/critical case, and missing or
unreadable data. Use the `make_ctx` fixture in `tests/conftest.py`, which builds a fake root:

```python
def test_thing_warns(make_ctx):
    ctx = make_ctx({"/proc/something": "value 99\n"})
    assert thing(ctx).status is Status.WARNING
```

Run: `pytest`. CI runs Python 3.9 to 3.13.

If you can, also run `linuxpilot -v` on real hardware (especially Raspberry Pi and non-Debian
distributions) and paste the output in your PR.
