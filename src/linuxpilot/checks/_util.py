"""Small parsing and formatting helpers shared by checks."""

from __future__ import annotations


def human_bytes(n: float) -> str:
    """1536 -> '1.5 KB' (binary units, short labels users expect from df -h)."""
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    value = float(n)
    for unit in units:
        if abs(value) < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PB"  # pragma: no cover


def human_duration(seconds: float) -> str:
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days} day{'s' if days != 1 else ''}, {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def parse_os_release(text: str) -> dict[str, str]:
    """Parse /etc/os-release KEY=value lines, removing optional quotes."""
    result: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        result[key.strip()] = value
    return result


def parse_meminfo(text: str) -> dict[str, int]:
    """Parse /proc/meminfo into bytes. Lines look like 'MemTotal:  8000000 kB'."""
    result: dict[str, int] = {}
    for line in text.splitlines():
        key, sep, rest = line.partition(":")
        if not sep:
            continue
        parts = rest.split()
        if not parts:
            continue
        try:
            value = int(parts[0])
        except ValueError:
            continue
        if len(parts) > 1 and parts[1].lower() == "kb":
            value *= 1024
        result[key.strip()] = value
    return result


def parse_psi(text: str) -> dict[str, dict[str, float]]:
    """Parse /proc/pressure/* lines: 'some avg10=0.00 avg60=1.50 avg300=0.10 total=123'."""
    result: dict[str, dict[str, float]] = {}
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        kind = parts[0]
        values: dict[str, float] = {}
        for item in parts[1:]:
            k, _, v = item.partition("=")
            try:
                values[k] = float(v)
            except ValueError:
                continue
        result[kind] = values
    return result
