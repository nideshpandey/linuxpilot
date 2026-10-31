"""Turn findings into terminal text or JSON. No system access happens here.

Shared pieces (verdict text, usage bars, sparklines, value formatting) are used
by the one-shot report and by the interactive terminal view in tui.py.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import sys
import textwrap
from typing import Any, TextIO

from linuxpilot import __version__
from linuxpilot.models import SEVERITY, Finding, Status, worst_status

_SYMBOLS_UNICODE = {
    Status.OK: "✔",
    Status.WARNING: "⚠",
    Status.CRITICAL: "✖",
    Status.UNKNOWN: "?",
    Status.UNSUPPORTED: "–",
    Status.SKIPPED: "–",
}
_SYMBOLS_ASCII = {
    Status.OK: "[ OK ]",
    Status.WARNING: "[WARN]",
    Status.CRITICAL: "[CRIT]",
    Status.UNKNOWN: "[ ?? ]",
    Status.UNSUPPORTED: "[ -- ]",
    Status.SKIPPED: "[ -- ]",
}
COLORS = {
    Status.OK: "32",  # green
    Status.WARNING: "33",  # yellow
    Status.CRITICAL: "31",  # red
    Status.UNKNOWN: "35",  # magenta
    Status.UNSUPPORTED: "90",  # grey
    Status.SKIPPED: "90",
}
_COLORS = COLORS  # backwards-compatible name
WORDS = {
    Status.OK: "OK",
    Status.WARNING: "Warning",
    Status.CRITICAL: "Critical",
    Status.UNKNOWN: "Unknown",
    Status.UNSUPPORTED: "N/A",
    Status.SKIPPED: "Skipped",
}

BAR_WIDTH = 10
_SPARK_UNICODE = "▁▂▃▄▅▆▇█"
_SPARK_ASCII = "_.-=+*#"


def use_color(stream: TextIO, disabled: bool) -> bool:
    if disabled or os.environ.get("NO_COLOR"):
        return False
    return hasattr(stream, "isatty") and stream.isatty()


def use_unicode(stream: TextIO) -> bool:
    encoding = (getattr(stream, "encoding", None) or "").lower()
    return "utf" in encoding


def symbols(unicode: bool) -> dict[Status, str]:
    return _SYMBOLS_UNICODE if unicode else _SYMBOLS_ASCII


class Painter:
    def __init__(self, color: bool) -> None:
        self.color = color

    def __call__(self, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.color and code else text

    def bold(self, text: str) -> str:
        return self(text, "1")

    def dim(self, text: str) -> str:
        return self(text, "90")


_Painter = Painter  # backwards-compatible name


# ---------------------------------------------------------------- shared pieces


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def verdict(findings: list[Finding]) -> tuple[Status, str]:
    """A plain-language headline, e.g. (WARNING, '2 warnings need attention')."""
    crit = sum(f.status is Status.CRITICAL for f in findings)
    warn = sum(f.status is Status.WARNING for f in findings)
    unknown = sum(f.status is Status.UNKNOWN for f in findings)
    if crit:
        text = f"{crit} critical {_plural(crit, 'problem', 'problems')}"
        if warn:
            text += f" and {warn} {_plural(warn, 'warning', 'warnings')}"
        return Status.CRITICAL, text + (" needs" if crit + warn == 1 else " need") + " attention"
    if warn:
        return Status.WARNING, f"{warn} {_plural(warn, 'warning needs', 'warnings need')} attention"
    if not any(f.status.is_applicable for f in findings):
        return Status.UNKNOWN, "None of the selected checks apply here"
    if unknown:
        return Status.OK, f"No problems found, {unknown} {_plural(unknown, 'check', 'checks')} could not run"
    return Status.OK, "All clear"


def meter_percent(f: Finding) -> float | None:
    """A 0-100 'how full' value for checks where a usage bar makes sense."""
    if not f.status.is_applicable:
        return None
    d = f.details or {}
    value = None
    if f.check_id == "disk.usage":
        if d.get("read_only") and not f.status.is_problem:
            return None  # intentionally read-only: nothing can fill it
        value = d.get("used_percent")
    elif f.check_id == "memory.usage":
        avail = d.get("available_percent")
        value = None if avail is None else 100 - avail
    elif f.check_id == "cpu.usage":
        value = d.get("busy_percent")
    if isinstance(value, (int, float)):
        return max(0.0, min(100.0, float(value)))
    return None


def bar(percent: float, width: int, unicode: bool) -> tuple[str, str]:
    """Return (filled, empty) parts of a usage bar so callers can colour them."""
    filled = int(round(percent / 100 * width))
    if percent > 0 and filled == 0:
        filled = 1
    if unicode:
        return "█" * filled, "░" * (width - filled)
    return "#" * filled, "." * (width - filled)


def sparkline(values: list[float], unicode: bool, lo: float | None = None, hi: float | None = None) -> str:
    if not values:
        return ""
    chars = _SPARK_UNICODE if unicode else _SPARK_ASCII
    lo = min(values) if lo is None else lo
    hi = max(values) if hi is None else hi
    span = hi - lo
    if span <= 0:
        return chars[len(chars) // 2] * len(values) if lo > 0 else chars[0] * len(values)
    out = []
    for v in values:
        pos = (min(max(v, lo), hi) - lo) / span
        out.append(chars[min(len(chars) - 1, int(pos * (len(chars) - 1) + 0.5))])
    return "".join(out)


def human_bytes(n: float) -> str:
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    i = 0
    value = float(n)
    while abs(value) >= 1024 and i < len(units) - 1:
        value /= 1024
        i += 1
    return f"{int(value)} B" if i == 0 else f"{value:.1f} {units[i]}"


def format_value(name: str, value: Any) -> str:
    if value is None or value == "":
        return "not available"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)) and name.endswith("_bytes"):
        return human_bytes(value)
    if isinstance(value, list):
        if not value:
            return "none"
        if all(not isinstance(v, (dict, list)) for v in value):
            return ", ".join(str(v) for v in value)
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
        return text if len(text) <= 200 else text[:197] + "..."
    return str(value)


def commands_in(text: str | None) -> list[str]:
    """Commands written as `inline code` in a recommendation."""
    return re.findall(r"`([^`]+)`", text or "")


def _wrap(label: str, text: str, width: int) -> list[str]:
    indent = "     "
    return textwrap.wrap(
        text,
        width=width,
        initial_indent=indent + label,
        subsequent_indent=indent + " " * len(label),
        break_on_hyphens=False,
        break_long_words=False,
    )


def header_bits(findings: list[Finding]) -> list[str]:
    parts = [socket.gethostname()]
    info = next((f for f in findings if f.check_id == "system.info"), None)
    if info is not None:
        if info.details.get("os"):
            parts.append(str(info.details["os"]))
        if info.details.get("uptime_human"):
            parts.append(f"up {info.details['uptime_human']}")
    return parts


def _header(findings: list[Finding], sep: str) -> str:
    return f" {sep} ".join([f"LinuxPilot {__version__}"] + header_bits(findings))


# ---------------------------------------------------------------- one-shot report


def render_human(
    findings: list[Finding],
    duration: float,
    *,
    stream: TextIO = sys.stdout,
    color: bool = False,
    unicode: bool = True,
    verbose: bool = False,
    tip: bool = False,
) -> str:
    paint = Painter(color)
    syms = symbols(unicode)
    sep = "·" if unicode else "|"
    lines: list[str] = []

    lines.append(paint.bold(_header(findings, sep)) + paint.dim(f"   checked in {duration:.1f}s"))
    lines.append("")

    v_status, v_text = verdict(findings)
    lines.append("  " + paint(syms[v_status], COLORS[v_status]) + " " + paint(v_text, f"1;{COLORS[v_status]}"))
    lines.append("")

    shown = [f for f in findings if verbose or f.status.is_applicable]
    hidden = [f for f in findings if not f.status.is_applicable and not verbose]
    width = min(max((len(f.title) for f in shown), default=10), 22)
    has_meters = any(meter_percent(f) is not None for f in shown)

    for f in shown:
        sym = paint(syms[f.status], COLORS[f.status])
        title = f.title.ljust(width)
        meter = ""
        if has_meters:
            pct = meter_percent(f)
            if pct is None:
                meter = " " * BAR_WIDTH + "  "
            else:
                filled, empty = bar(pct, BAR_WIDTH, unicode)
                meter = paint(filled, COLORS[f.status]) + paint.dim(empty) + "  "
        summary = f.summary if f.status.is_applicable else paint.dim(f.summary)
        lines.append(f"  {sym} {title}  {meter}{summary}")
        if verbose:
            for key, value in f.details.items():
                lines.append(paint.dim(f"      {key}: {format_value(key, value)}"))
            for lim in f.limitations:
                lines.append(paint.dim(f"      note: {lim}"))

    width_limit = max(60, min(shutil.get_terminal_size((100, 24)).columns, 120))
    problems = sorted(
        (f for f in findings if f.status.is_problem or f.status is Status.UNKNOWN),
        key=lambda f: -SEVERITY[f.status],
    )
    if problems:
        lines.append("")
        lines.append(paint.bold("Needs attention"))
        for i, f in enumerate(problems, 1):
            tag = paint(f.status.value.upper(), COLORS[f.status])
            lines.append(f"  {i}. [{tag}] {f.title}: {f.summary}")
            if f.reason:
                lines.extend(_wrap("Why:  ", f.reason, width_limit))
            if f.recommendation:
                lines.extend(_wrap("Next: ", f.recommendation, width_limit))
            if f.status is Status.UNKNOWN and f.limitations and not verbose:
                lines.append(paint.dim(f"     Note: {'; '.join(f.limitations)}"))

    overall = worst_status(findings)
    counts = {s: sum(1 for f in findings if f.status is s) for s in Status}
    summary_bits = [
        f"{counts[Status.CRITICAL]} critical",
        f"{counts[Status.WARNING]} warning",
        f"{counts[Status.OK]} ok",
    ]
    if counts[Status.UNKNOWN]:
        summary_bits.append(f"{counts[Status.UNKNOWN]} unknown")
    lines.append("")
    if any(f.status.is_applicable for f in findings):
        lines.append(
            paint.bold("Overall: ")
            + paint(overall.value.upper(), COLORS[overall])
            + f"  {sep}  "
            + ", ".join(summary_bits)
        )
    else:
        lines.append(paint.bold("Overall: ") + "none of the selected checks apply to this system")
    if hidden:
        lines.append(
            paint.dim(f"{len(hidden)} check(s) not applicable or skipped on this system (use -v to list them)")
        )
    if tip:
        lines.append(paint.dim("Tip: run `linuxpilot watch` for a live, interactive view (q to quit)."))
    return "\n".join(lines) + "\n"


def to_json_obj(findings: list[Finding], duration: float) -> dict[str, Any]:
    overall = worst_status(findings)
    return {
        "tool": "linuxpilot",
        "version": __version__,
        "hostname": socket.gethostname(),
        "overall_status": overall.value,
        "duration_seconds": round(duration, 3),
        "findings": [f.to_dict() for f in findings],
    }


def render_json(findings: list[Finding], duration: float) -> str:
    return json.dumps(to_json_obj(findings, duration), indent=2) + "\n"
