"""Memory, swap, memory pressure and OOM-killer activity."""

from __future__ import annotations

from linuxpilot.checks._util import human_bytes, parse_meminfo, parse_psi
from linuxpilot.context import Context
from linuxpilot.models import Finding, Status
from linuxpilot.registry import check


@check("memory.usage", "Memory")
def memory_usage(ctx: Context) -> Finding:
    """Available memory, swap use and memory pressure."""
    try:
        info = parse_meminfo(ctx.read("/proc/meminfo"))
    except OSError as exc:
        return Finding("memory.usage", "Memory", Status.UNKNOWN, "could not read memory information",
                       limitations=[f"/proc/meminfo: {exc}"])

    total = info.get("MemTotal")
    if not total:
        return Finding("memory.usage", "Memory", Status.UNKNOWN, "MemTotal missing from /proc/meminfo",
                       limitations=["unexpected /proc/meminfo format"])

    limitations = []
    available = info.get("MemAvailable")
    if available is None:  # kernels older than 3.14
        available = info.get("MemFree", 0) + info.get("Buffers", 0) + info.get("Cached", 0)
        limitations.append("MemAvailable not provided by this kernel; estimated from free + buffers + cache")

    used = total - available
    avail_pct = 100.0 * available / total
    swap_total = info.get("SwapTotal", 0)
    swap_used = swap_total - info.get("SwapFree", 0) if swap_total else 0

    pressure = None
    psi_text = ctx.read_or_none("/proc/pressure/memory")
    if psi_text:
        pressure = parse_psi(psi_text).get("some", {}).get("avg60")

    t = ctx.thresholds
    status = Status.OK
    if avail_pct < t["memory_available_critical_percent"]:
        status = Status.CRITICAL
    elif avail_pct < t["memory_available_warning_percent"]:
        status = Status.WARNING
    high_pressure = pressure is not None and pressure >= t["memory_pressure_warning_percent"]
    if high_pressure and status is Status.OK:
        status = Status.WARNING

    summary = f"{human_bytes(used)} / {human_bytes(total)} used ({100 - avail_pct:.0f}%)"
    if swap_total:
        summary += f", swap {human_bytes(swap_used)} / {human_bytes(swap_total)}"
    else:
        summary += ", no swap"
    if pressure is not None and pressure >= 1:
        summary += f", pressure {pressure:.0f}%"

    finding = Finding(
        check_id="memory.usage",
        title="Memory",
        status=status,
        summary=summary,
        details={
            "total_bytes": total,
            "available_bytes": available,
            "used_bytes": used,
            "available_percent": round(avail_pct, 1),
            "swap_total_bytes": swap_total,
            "swap_used_bytes": swap_used,
            "pressure_some_avg60": pressure,
        },
        source="/proc/meminfo, /proc/pressure/memory",
        limitations=limitations,
    )
    if status.is_problem:
        parts = []
        if avail_pct < t["memory_available_warning_percent"]:
            parts.append(f"Only {avail_pct:.0f}% of memory is available. When it runs out, the kernel's OOM killer "
                         "terminates processes, often a database or web server.")
        if high_pressure:
            parts.append(f"Processes spent {pressure:.0f}% of the last minute stalled waiting for memory, "
                         "which makes the system feel slow even before memory is fully used.")
        finding.reason = " ".join(parts)
        rec = "Find the largest processes with `ps aux --sort=-rss | head`."
        if not swap_total:
            rec += " This system has no swap; a small swap file can absorb short spikes."
        finding.recommendation = rec
    return finding


@check("memory.oom", "OOM kills")
def oom_kills(ctx: Context) -> Finding:
    """Processes killed by the out-of-memory killer since boot."""
    text = ctx.read_or_none("/proc/vmstat")
    if text is None:
        return Finding("memory.oom", "OOM kills", Status.UNKNOWN, "could not read /proc/vmstat",
                       limitations=["/proc/vmstat not readable"])
    count = None
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "oom_kill":
            try:
                count = int(parts[1])
            except ValueError:
                pass
            break
    if count is None:
        return Finding("memory.oom", "OOM kills", Status.UNSUPPORTED,
                       "kernel does not report an OOM-kill counter (requires Linux 4.13+)")
    if count == 0:
        return Finding("memory.oom", "OOM kills", Status.OK, "no processes killed for lack of memory since boot",
                       details={"oom_kills_since_boot": 0}, source="/proc/vmstat")
    return Finding(
        check_id="memory.oom",
        title="OOM kills",
        status=Status.WARNING,
        summary=f"{count} process{'es' if count != 1 else ''} killed by the OOM killer since boot",
        details={"oom_kills_since_boot": count},
        reason="The system ran out of memory at some point and the kernel killed processes to recover. "
        "Services may have restarted, crashed, or lost work without an obvious error.",
        recommendation="See which processes were killed: `journalctl -k -b | grep -i 'killed process'` "
        "(or `sudo dmesg | grep -i oom`).",
        source="/proc/vmstat (oom_kill)",
    )
