"""Interactive terminal view for `linuxpilot watch`.

Standard library only: termios puts the terminal in cbreak mode (keys arrive
one at a time, Ctrl+C still works), and the screen is drawn with plain ANSI
escape codes. Checks run in a background thread so keys stay responsive.

The View class holds all state and has no I/O, so it is unit-tested directly.
run() is the thin loop that connects it to a real terminal.
"""

from __future__ import annotations

import base64
import os
import queue
import select
import signal
import sys
import termios
import textwrap
import threading
import time
import tty
from dataclasses import dataclass, field
from typing import Callable, TextIO

from linuxpilot.models import SEVERITY, Finding, Status
from linuxpilot.render import (
    BAR_WIDTH,
    COLORS,
    WORDS,
    bar,
    commands_in,
    format_value,
    header_bits,
    meter_percent,
    sparkline,
    symbols,
    verdict,
)
from linuxpilot.runner import RunResult

INTERVALS = [2, 5, 10, 30, 60]
FILTERS = ["all", "problems", "ok"]
FILTER_NAMES = {"all": "All", "problems": "Problems", "ok": "OK"}
HISTORY = 60
CHANGED_SECONDS = 10
MIN_WIDTH, MIN_HEIGHT = 40, 10

UP, DOWN, LEFT, RIGHT = "up", "down", "left", "right"
PGUP, PGDN, HOME, END = "pgup", "pgdn", "home", "end"
ENTER, ESC, BACKSPACE, TAB = "enter", "esc", "backspace", "tab"

_SEQUENCES = {
    "\x1b[A": UP, "\x1b[B": DOWN, "\x1b[C": RIGHT, "\x1b[D": LEFT,
    "\x1bOA": UP, "\x1bOB": DOWN, "\x1bOC": RIGHT, "\x1bOD": LEFT,
    "\x1b[5~": PGUP, "\x1b[6~": PGDN,
    "\x1b[H": HOME, "\x1b[F": END, "\x1b[1~": HOME, "\x1b[4~": END, "\x1bOH": HOME, "\x1bOF": END,
}

HELP = [
    ("Up/Down, j/k", "move between checks"),
    ("Enter or Space", "open or close the details of a check"),
    ("Right / Left", "open / close details"),
    ("PgUp/PgDn, Home/End", "move faster, jump to first / last"),
    ("e", "open or close all checks"),
    ("f or Tab", "show all checks, only problems, or only OK"),
    ("/", "search by name (Enter keeps it, Esc clears it)"),
    ("r", "check again now"),
    ("p", "pause or resume live updates"),
    ("+ / -", "refresh less often / more often"),
    ("c", "copy the suggested command (if your terminal allows it)"),
    ("v", "show or hide checks that do not apply here"),
    ("q or Esc", "quit"),
]

METRICS = [
    ("cpu", "CPU", "cpu.usage", (0, 100)),
    ("mem", "Memory", "memory.usage", (0, 100)),
    ("load", "Load", "cpu.load", None),
    ("temp", "Temp", "cpu.temperature", None),
]


def parse_keys(data: str) -> list[str]:
    """Turn raw terminal input into key names ('up', 'enter', 'q', ...)."""
    keys: list[str] = []
    i = 0
    while i < len(data):
        ch = data[i]
        if ch == "\x1b":
            for seq, name in _SEQUENCES.items():
                if data.startswith(seq, i):
                    keys.append(name)
                    i += len(seq)
                    break
            else:
                if i + 1 < len(data) and data[i + 1] in "[O":
                    j = i + 2  # skip an unknown escape sequence
                    while j < len(data) and not (data[j].isalpha() or data[j] == "~"):
                        j += 1
                    i = j + 1
                else:
                    keys.append(ESC)
                    i += 1
            continue
        if ch in "\r\n":
            keys.append(ENTER)
        elif ch in "\x7f\x08":
            keys.append(BACKSPACE)
        elif ch == "\t":
            keys.append(TAB)
        elif ch >= " ":
            keys.append(ch)
        i += 1
    return keys


def key_of(f: Finding) -> str:
    return f"{f.check_id}|{f.title}"


def _metric_value(metric_id: str, f: Finding) -> float | None:
    d = f.details or {}
    if metric_id == "cpu":
        v = d.get("busy_percent")
    elif metric_id == "mem":
        v = None if d.get("available_percent") is None else 100 - d["available_percent"]
    elif metric_id == "load":
        v = d.get("load1")
    else:
        v = d.get("celsius")
    return float(v) if isinstance(v, (int, float)) else None


def _metric_text(metric_id: str, v: float) -> str:
    if metric_id in ("cpu", "mem"):
        return f"{v:.0f}%"
    if metric_id == "load":
        return f"{v:.2f}"
    return f"{v:.0f}°C"


# ---------------------------------------------------------------- state


@dataclass
class View:
    interval: float = 5.0
    findings: list[Finding] = field(default_factory=list)
    duration: float = 0.0
    updated_at: float | None = None
    paused: bool = False
    busy: bool = False
    filter: str = "all"
    query: str = ""
    searching: bool = False
    show_na: bool = False
    help: bool = False
    selected: int = 0
    offset: int = 0
    expanded: set = field(default_factory=set)
    changed: dict = field(default_factory=dict)
    previous: dict = field(default_factory=dict)
    history: dict = field(default_factory=lambda: {m[0]: [] for m in METRICS})
    latest: dict = field(default_factory=dict)
    message: str = ""
    message_until: float = 0.0
    first: bool = True

    # ----- data
    def apply(self, result: RunResult, now: float | None = None) -> None:
        now = time.time() if now is None else now
        current = self.current()
        for f in result.findings:
            before = self.previous.get(key_of(f))
            if before is not None and before != f.status:
                self.changed[key_of(f)] = now
        self.previous = {key_of(f): f.status for f in result.findings}
        self.findings = result.findings
        self.duration = result.duration
        self.updated_at = now
        if self.first:
            # Open problems on first load so the explanation is visible at once.
            self.expanded |= {key_of(f) for f in self.findings if f.status.is_problem}
            self.first = False
            # Start on the most serious problem, if there is one.
            items = self.visible()
            worst = sorted((i for i, f in enumerate(items) if f.status.is_problem),
                           key=lambda i: -SEVERITY[items[i].status])
            if worst:
                self.selected = worst[0]
                current = items[worst[0]]
        for metric_id, _label, check_id, _range in METRICS:
            f = next((x for x in self.findings if x.check_id == check_id and x.status.is_applicable), None)
            v = _metric_value(metric_id, f) if f else None
            if v is None:
                continue
            series = self.history[metric_id]
            series.append(v)
            del series[:-HISTORY]
            self.latest[metric_id] = f
        # Keep the cursor on the same check after a refresh.
        items = self.visible()
        if current is not None:
            for i, f in enumerate(items):
                if key_of(f) == key_of(current):
                    self.selected = i
                    break
        self.clamp()

    def visible(self) -> list[Finding]:
        items = [f for f in self.findings if f.status.is_applicable]
        if self.show_na:
            items += [f for f in self.findings if not f.status.is_applicable]
        if self.filter == "problems":
            items = [f for f in items if f.status.is_problem or f.status is Status.UNKNOWN]
        elif self.filter == "ok":
            items = [f for f in items if f.status is Status.OK]
        if self.query:
            q = self.query.lower()
            items = [f for f in items if q in f"{f.title} {f.summary}".lower()]
        return items

    def current(self) -> Finding | None:
        items = self.visible()
        return items[self.selected] if 0 <= self.selected < len(items) else None

    def clamp(self) -> None:
        n = len(self.visible())
        self.selected = max(0, min(self.selected, n - 1)) if n else 0

    def say(self, text: str, seconds: float = 3.0) -> None:
        self.message = text
        self.message_until = time.time() + seconds

    # ----- keys
    def handle_key(self, key: str, page: int = 10):
        """Update state for one key. Returns None, 'quit', 'refresh', 'reschedule' or ('copy', text)."""
        if self.help:
            self.help = False
            return "quit" if key == "q" else None

        if self.searching:
            if key == ENTER:
                self.searching = False
                return None
            if key == ESC:
                self.searching = False
                self.query = ""
                self.clamp()
                return None
            if key == BACKSPACE:
                self.query = self.query[:-1]
                self.selected = 0
                return None
            if len(key) == 1:
                self.query += key
                self.selected = 0
                return None
            # arrows etc. still move while searching

        n = len(self.visible())
        if key in (UP, "k"):
            self.selected = max(0, self.selected - 1)
        elif key in (DOWN, "j"):
            self.selected = min(max(n - 1, 0), self.selected + 1)
        elif key == PGUP:
            self.selected = max(0, self.selected - page)
        elif key == PGDN:
            self.selected = min(max(n - 1, 0), self.selected + page)
        elif key in (HOME, "g"):
            self.selected = 0
        elif key in (END, "G"):
            self.selected = max(n - 1, 0)
        elif key in (ENTER, " ", RIGHT, "l", LEFT, "h"):
            f = self.current()
            if f is not None:
                k = key_of(f)
                if key in (LEFT, "h"):
                    self.expanded.discard(k)
                elif key in (RIGHT, "l"):
                    self.expanded.add(k)
                elif k in self.expanded:
                    self.expanded.discard(k)
                else:
                    self.expanded.add(k)
        elif key == "e":
            keys = {key_of(f) for f in self.visible()}
            if keys and keys <= self.expanded:
                self.expanded -= keys
            else:
                self.expanded |= keys
        elif key in ("f", TAB):
            self.filter = FILTERS[(FILTERS.index(self.filter) + 1) % len(FILTERS)]
            self.selected = 0
            self.say(f"Showing: {FILTER_NAMES[self.filter]}")
        elif key in ("1", "2", "3"):
            self.filter = FILTERS[int(key) - 1]
            self.selected = 0
        elif key == "/":
            self.searching = True
        elif key == "r":
            self.say("Checking again")
            return "refresh"
        elif key == "p":
            self.paused = not self.paused
            self.say("Paused. Press p to resume, r to check once." if self.paused else "Live updates resumed")
            return None if self.paused else "refresh"
        elif key in ("+", "="):
            bigger = [s for s in INTERVALS if s > self.interval]
            if bigger:
                self.interval = bigger[0]
            self.say(f"Refreshing every {self.interval:g} s")
            return "reschedule"
        elif key in ("-", "_"):
            smaller = [s for s in INTERVALS if s < self.interval]
            if smaller:
                self.interval = smaller[-1]
            self.say(f"Refreshing every {self.interval:g} s")
            return "reschedule"
        elif key == "c":
            f = self.current()
            cmds = commands_in(f.recommendation) if f else []
            if not cmds:
                self.say("This check has no command to copy.")
                return None
            self.say(f"Copied: {cmds[0]}  (needs a terminal that allows copying)", 4)
            return ("copy", cmds[0])
        elif key == "v":
            self.show_na = not self.show_na
            self.clamp()
        elif key == "?":
            self.help = True
        elif key == ESC:
            if self.query:
                self.query = ""
                self.clamp()
            else:
                return "quit"
        elif key == "q":
            return "quit"
        self.clamp()
        return None


# ---------------------------------------------------------------- drawing


class Line:
    """A screen line made of (text, ansi_code) parts, truncated to width when drawn."""

    def __init__(self) -> None:
        self.parts: list[tuple[str, str]] = []

    def add(self, text: str, code: str = "") -> "Line":
        if text:
            self.parts.append((text, code))
        return self

    @property
    def width(self) -> int:
        return sum(len(t) for t, _ in self.parts)

    def draw(self, width: int, color: bool, ellipsis: str, fill_code: str = "") -> str:
        out, used = [], 0
        for text, code in self.parts:
            room = width - used
            if room <= 0:
                break
            if len(text) > room:
                text = text[: room - 1] + ellipsis if room > 1 else text[:room]
            used += len(text)
            if fill_code:
                code = f"{fill_code};{code}" if code else fill_code
            out.append(f"\033[{code}m{text}\033[0m" if color and code else text)
        if fill_code and color and used < width:
            out.append(f"\033[{fill_code}m{' ' * (width - used)}\033[0m")
        return "".join(out)


def _code_parts(text: str, in_code: bool) -> tuple[list[tuple[str, str]], bool]:
    """Split text at backticks; code spans are cyan. Tracks spans across wrapped lines."""
    parts = []
    for i, chunk in enumerate(text.split("`")):
        if i > 0:
            in_code = not in_code
        if chunk:
            parts.append((chunk, "36" if in_code else ""))
    return parts, in_code


def _wrapped(label: str, text: str, width: int, indent: int) -> list[Line]:
    lines = []
    body_width = max(10, width - indent - len(label))
    in_code = False
    for i, chunk in enumerate(textwrap.wrap(text, body_width, break_long_words=True, break_on_hyphens=False)):
        line = Line().add(" " * indent)
        line.add(label if i == 0 else " " * len(label), "90" if i == 0 else "")
        parts, in_code = _code_parts(chunk, in_code)
        for t, c in parts:
            line.add(t, c)
        lines.append(line)
    return lines


def _detail_lines(f: Finding, width: int, unicode: bool) -> list[Line]:
    indent = 6
    out: list[Line] = []
    if f.reason:
        out += _wrapped("Why:  ", f.reason, width, indent)
    if f.recommendation:
        out += _wrapped("Next: ", f.recommendation, width, indent)
    shown = 0
    for name, value in (f.details or {}).items():
        if shown >= 10:
            out.append(Line().add(" " * indent).add(f"(+{len(f.details) - 10} more values, see --json)", "90"))
            break
        label = name.replace("_", " ")
        nested = isinstance(value, dict) or (isinstance(value, list) and any(isinstance(v, (dict, list)) for v in value))
        if nested:
            text = f"{len(value)} {'entry' if len(value) == 1 else 'entries'} (full data: linuxpilot --json)"
            out.append(Line().add(" " * indent).add(f"{label}: ", "90").add(text, "90"))
        else:
            out += _wrapped(f"{label}: ", format_value(name, value), width, indent)
        shown += 1
    for note in f.limitations:
        out += _wrapped("Note: ", note, width, indent)
    if f.source:
        out += _wrapped("From: ", f.source, width, indent)
    if commands_in(f.recommendation):
        out.append(Line().add(" " * indent).add("Press c to copy the first command.", "90"))
    out.append(Line())
    return out


def build_frame(view: View, width: int, height: int, *, color: bool, unicode: bool, now: float | None = None) -> list[str]:
    """Return exactly `height` lines describing the whole screen."""
    now = time.time() if now is None else now
    ell = "…" if unicode else "~"
    syms = symbols(unicode)
    rule_ch = "─" if unicode else "-"

    def draw(line: Line, fill: str = "") -> str:
        return line.draw(width, color, ell, fill)

    if width < MIN_WIDTH or height < MIN_HEIGHT:
        lines = ["Window too small.", f"Make it at least {MIN_WIDTH} x {MIN_HEIGHT}, or press q to quit."]
        return [l[:width] for l in lines] + [""] * (height - len(lines))

    top: list[str] = []

    # Header: name and machine on the left, live status on the right.
    left = Line().add(" LinuxPilot", "1").add("  " + ", ".join(header_bits(view.findings)), "90")
    if view.busy:
        frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏" if unicode else "-\\|/"
        state = f"{frames[int(now * 10) % len(frames)]} checking"
        state_code = "36"
    elif view.paused:
        state, state_code = ("❚❚ paused" if unicode else "|| paused"), "33"
    else:
        ago = "" if view.updated_at is None else f", updated {max(0, int(now - view.updated_at))}s ago"
        state, state_code = f"{'●' if unicode else '*'} live, every {view.interval:g}s{ago}", "32"
    pad = width - left.width - len(state) - 1
    if pad >= 2:
        left.add(" " * pad).add(state, state_code)
    top.append(draw(left))
    top.append("")

    # Verdict headline.
    if not view.findings:
        top.append(draw(Line().add(" Checking this machine...", "1")))
    else:
        v_status, v_text = verdict(view.findings)
        applicable = [f for f in view.findings if f.status.is_applicable]
        ok = sum(f.status is Status.OK for f in applicable)
        top.append(draw(Line().add(" ").add(syms[v_status] + " ", COLORS[v_status])
                        .add(v_text, f"1;{COLORS[v_status]}")
                        .add(f"   {ok} of {len(applicable)} checks OK, took {view.duration:.1f}s", "90")))
    top.append("")

    # Trends with sparklines; fewer points (then fewer metrics) on narrow screens.
    trends_line = Line()
    for points in (16, 10, 6):
        trends_line = Line().add(" ")
        fitted = True
        for metric_id, label, _check, rng in METRICS:
            series = view.history[metric_id]
            if not series:
                continue
            f = view.latest.get(metric_id)
            code = COLORS[f.status] if f else ""
            lo, hi = rng if rng else (None, None)
            if metric_id == "load" and f is not None:
                lo, hi = 0, max(max(series), float(f.details.get("cores") or 1))
            item = Line().add(label + " ", "90").add(sparkline(series[-points:], unicode, lo, hi), code) \
                         .add(" " + _metric_text(metric_id, series[-1]), "1")
            if trends_line.width + item.width + 3 > width:
                fitted = False
                break
            if trends_line.width > 1:
                trends_line.add("   ")
            trends_line.parts += item.parts
        if fitted:
            break
    if trends_line.width <= 1 and view.findings:
        trends_line.add("Trends appear after a few refreshes.", "90")
    top.append(draw(trends_line))
    top.append("")

    # Filter tabs and search.
    items = view.visible()
    applicable = [f for f in view.findings if f.status.is_applicable]
    counts = {
        "all": len(applicable) + (len(view.findings) - len(applicable) if view.show_na else 0),
        "problems": sum(f.status.is_problem or f.status is Status.UNKNOWN for f in applicable),
        "ok": sum(f.status is Status.OK for f in applicable),
    }
    tabs = Line().add(" ")
    for name in FILTERS:
        label = f" {FILTER_NAMES[name]} {counts[name]} "
        tabs.add(label, "1;7" if view.filter == name else "")
        tabs.add(" ")
    if view.searching or view.query:
        tabs.add("  Search: ", "90").add(view.query, "1")
        if view.searching:
            tabs.add("▏" if unicode else "_", "36")
    top.append(draw(tabs))

    footer_rows = 2
    list_rows = height - len(top) - 1 - footer_rows

    # Body: help, empty state, or the list of checks.
    body: list[tuple[Line, int | None]] = []
    if view.help:
        body.append((Line().add(" Keys", "1"), None))
        kw = max(len(k) for k, _ in HELP) + 3
        for k, desc in HELP:
            body.append((Line().add("   " + k.ljust(kw), "36").add(desc), None))
        body.append((Line(), None))
        body.append((Line().add("   Press any key to close this help.", "90"), None))
    elif not view.findings:
        body.append((Line().add("   Running checks...", "90"), None))
    elif not items:
        msg = "No problems right now." if view.filter == "problems" and not view.query else "No checks match."
        body.append((Line().add("   " + msg).add("  Press f to change the filter or Esc to clear the search.", "90"), None))
    else:
        tw = min(max(len(f.title) for f in items), 22)
        meters = any(meter_percent(f) is not None for f in items)
        for i, f in enumerate(items):
            k = key_of(f)
            open_ = k in view.expanded
            selected = i == view.selected and not view.help
            line = Line()
            line.add(("›" if unicode else ">") if selected else " ", "1;36")
            line.add((("▾" if open_ else "▸") if unicode else ("-" if open_ else "+")) + " ", "90")
            line.add(syms[f.status] + " ", COLORS[f.status])
            line.add(WORDS[f.status].ljust(9), COLORS[f.status])
            if selected and color:
                # Only the name is highlighted, so status colours and bars stay readable.
                line.add(f" {f.title} ", "1;7").add(" " * (tw - len(f.title)) + " ")
            else:
                line.add(" " + f.title.ljust(tw) + "  ", "1" if f.status.is_problem or selected else "")
            if meters:
                pct = meter_percent(f)
                if pct is None:
                    line.add(" " * (BAR_WIDTH + 2))
                else:
                    filled, empty = bar(pct, BAR_WIDTH, unicode)
                    line.add(filled, COLORS[f.status]).add(empty + "  ", "90")
            line.add(f.summary, "" if f.status.is_applicable else "90")
            if now - view.changed.get(k, -1e9) < CHANGED_SECONDS:
                line.add("  changed", "1;36")
            body.append((line, i))
            if open_:
                for d in _detail_lines(f, width, unicode):
                    body.append((d, None))

    # Keep the selected check (and as much of its details as fits) on screen.
    sel_start = next((n for n, (_, idx) in enumerate(body) if idx == view.selected), 0)
    sel_end = sel_start
    for n in range(sel_start + 1, len(body)):
        if body[n][1] is not None:
            break
        sel_end = n
    if sel_start < view.offset:
        view.offset = sel_start
    elif sel_end >= view.offset + list_rows:
        view.offset = min(sel_start, sel_end - list_rows + 1)
    view.offset = max(0, min(view.offset, max(0, len(body) - list_rows)))
    if view.help:
        view.offset = 0

    above = view.offset
    below = max(0, len(body) - view.offset - list_rows)
    rule = Line().add(" " + rule_ch * max(0, width - 2), "90")
    up_note = f" {'↑' if unicode else '^'} {above} more " if above else ""
    if up_note:
        rule = Line().add(" " + rule_ch * max(0, width - 2 - len(up_note) - 2), "90").add(up_note, "90")
    top.append(draw(rule))

    shown_lines = [draw(line) for line, _ in body[view.offset: view.offset + list_rows]]
    shown_lines += [""] * (list_rows - len(shown_lines))

    # Footer: rule, then a message or the key hints.
    down_note = f" {'↓' if unicode else 'v'} {below} more " if below else ""
    bottom_rule = Line().add(" " + rule_ch * max(0, width - 2 - len(down_note) - (2 if down_note else 0)), "90")
    if down_note:
        bottom_rule.add(down_note, "90")
    if view.message and now < view.message_until:
        hint = Line().add(" " + view.message, "36")
    elif view.searching:
        hint = Line().add(" Type to search. Enter keeps the search, Esc clears it.", "90")
    else:
        arrows = "↑↓" if unicode else "up/down"
        full = [(arrows, "move"), ("Enter", "open"), ("f", "filter"), ("/", "search"), ("r", "refresh"),
                ("p", "pause"), ("+/-", "speed"), ("c", "copy"), ("?", "help"), ("q", "quit")]
        hint = Line().add(" ")
        for k, desc in full:
            piece = Line().add(k, "1").add(f" {desc}  ", "90")
            if hint.width + piece.width > width:
                break
            hint.parts += piece.parts

    return top + shown_lines + [draw(bottom_rule), draw(hint)]


# ---------------------------------------------------------------- terminal loop

_ALT_ON, _ALT_OFF = "\033[?1049h", "\033[?1049l"
_HIDE, _SHOW = "\033[?25l", "\033[?25h"


def _terminal_size(out: TextIO) -> tuple[int, int]:
    try:
        size = os.get_terminal_size(out.fileno())
        return size.columns, size.lines
    except (OSError, ValueError, AttributeError):
        return 80, 24


def run(
    provider: Callable[[], RunResult],
    interval: float,
    *,
    color: bool,
    unicode: bool,
    out: TextIO = sys.stdout,
    in_fd: int | None = None,
) -> None:
    """Run the interactive view until the user quits. Always restores the terminal."""
    in_fd = sys.stdin.fileno() if in_fd is None else in_fd
    view = View(interval=interval)
    results: queue.Queue = queue.Queue()
    worker: list[threading.Thread | None] = [None]
    saved = termios.tcgetattr(in_fd)  # raises termios.error if this is not a terminal

    def start_checks() -> None:
        if worker[0] is not None and worker[0].is_alive():
            return
        view.busy = True

        def job() -> None:
            try:
                results.put(("ok", provider()))
            except Exception as exc:  # keep the UI alive and say what happened
                results.put(("error", exc))

        worker[0] = threading.Thread(target=job, daemon=True)
        worker[0].start()

    def enter() -> None:
        tty.setcbreak(in_fd)
        out.write(_ALT_ON + _HIDE + "\033[2J")
        out.flush()

    def leave() -> None:
        out.write(_SHOW + _ALT_OFF)
        out.flush()
        termios.tcsetattr(in_fd, termios.TCSADRAIN, saved)

    state = {"redraw": True}

    def on_suspend(signum, frame) -> None:
        """Ctrl+Z: give the terminal back, stop, and take it again on `fg`."""
        leave()
        signal.signal(signal.SIGTSTP, signal.SIG_DFL)
        os.kill(os.getpid(), signal.SIGTSTP)
        # We get here after `fg`, or straight away if the system ignored the
        # stop (possible for orphaned process groups). Either way, resume.
        signal.signal(signal.SIGTSTP, on_suspend)
        enter()
        state["redraw"] = True

    def on_continue(signum, frame) -> None:
        state["redraw"] = True

    old_tstp = signal.signal(signal.SIGTSTP, on_suspend)
    old_cont = signal.signal(signal.SIGCONT, on_continue)
    enter()
    try:
        next_due = 0.0
        last_done = 0.0
        force = True
        last_draw = 0.0
        last_size = None
        while True:
            now = time.monotonic()
            if not view.busy and (force or (not view.paused and now >= next_due)):
                force = False
                start_checks()

            try:
                while True:
                    kind, payload = results.get_nowait()
                    view.busy = False
                    if kind == "ok":
                        view.apply(payload)
                    else:
                        view.say(f"Checks failed: {type(payload).__name__}: {payload}", 6)
                    last_done = time.monotonic()
                    next_due = last_done + view.interval
                    state["redraw"] = True
            except queue.Empty:
                pass

            size = _terminal_size(out)
            if state["redraw"] or size != last_size or now - last_draw >= (0.1 if view.busy else 1.0):
                w, h = size
                frame = build_frame(view, w, h, color=color, unicode=unicode)
                out.write("\033[H" + "\n".join(line + "\033[K" for line in frame) + "\033[J")
                out.flush()
                last_draw, last_size, state["redraw"] = now, size, False

            try:
                ready, _, _ = select.select([in_fd], [], [], 0.1)
            except InterruptedError:
                continue
            if not ready:
                continue
            data = os.read(in_fd, 1024).decode("utf-8", "replace")
            page = max(1, size[1] // 3)
            for k in parse_keys(data):
                action = view.handle_key(k, page=page)
                if action == "quit":
                    return
                if action == "refresh":
                    force = True
                elif action == "reschedule":
                    next_due = last_done + view.interval
                elif isinstance(action, tuple) and action[0] == "copy":
                    # OSC 52 asks the terminal to put text on the clipboard; works over SSH too.
                    payload = base64.b64encode(action[1].encode()).decode()
                    out.write(f"\033]52;c;{payload}\a")
            state["redraw"] = True
    finally:
        leave()
        signal.signal(signal.SIGTSTP, old_tstp)
        signal.signal(signal.SIGCONT, old_cont)
