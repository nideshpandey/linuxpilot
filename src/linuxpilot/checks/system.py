"""Operating system identity, uptime and reboot-required state."""

from __future__ import annotations

import platform

from linuxpilot.checks._util import human_duration, parse_os_release
from linuxpilot.context import Context
from linuxpilot.models import Finding, Status
from linuxpilot.registry import check


def _os_release(ctx: Context) -> dict[str, str]:
    for p in ("/etc/os-release", "/usr/lib/os-release"):
        text = ctx.read_or_none(p)
        if text:
            return parse_os_release(text)
    return {}


@check("system.info", "System")
def system_info(ctx: Context) -> Finding:
    """Operating system, kernel, architecture and uptime."""
    osr = _os_release(ctx)
    os_name = osr.get("PRETTY_NAME") or osr.get("NAME")
    kernel = (ctx.read_or_none("/proc/sys/kernel/osrelease") or "").strip() or None
    model = (ctx.read_or_none("/proc/device-tree/model") or "").replace("\x00", "").strip() or None

    uptime_seconds = None
    uptime_text = ctx.read_or_none("/proc/uptime")
    if uptime_text:
        try:
            uptime_seconds = float(uptime_text.split()[0])
        except (ValueError, IndexError):
            uptime_seconds = None

    details = {
        "os": os_name,
        "os_id": osr.get("ID"),
        "kernel": kernel,
        "architecture": platform.machine() or None,
        "hardware_model": model,
        "uptime_seconds": int(uptime_seconds) if uptime_seconds is not None else None,
        "uptime_human": human_duration(uptime_seconds) if uptime_seconds is not None else None,
    }
    limitations = []
    if not os_name:
        limitations.append("could not read /etc/os-release")
    if uptime_seconds is None:
        limitations.append("could not read /proc/uptime")

    bits = [b for b in (os_name, f"kernel {kernel}" if kernel else None, details["architecture"]) if b]
    if details["uptime_human"]:
        bits.append(f"up {details['uptime_human']}")
    return Finding(
        check_id="system.info",
        title="System",
        status=Status.OK,
        summary=", ".join(bits) or "system identity unavailable",
        details=details,
        source="/etc/os-release, /proc/sys/kernel/osrelease, /proc/uptime",
        limitations=limitations,
    )


@check("system.reboot", "Reboot required")
def reboot_required(ctx: Context) -> Finding:
    """Whether installed updates are waiting for a reboot (Debian/Ubuntu family)."""
    osr = _os_release(ctx)
    family = " ".join([osr.get("ID", ""), osr.get("ID_LIKE", "")]).lower()
    flag = "/var/run/reboot-required"
    if ctx.exists(flag) or ctx.exists("/run/reboot-required"):
        pkgs_text = ctx.read_or_none("/var/run/reboot-required.pkgs") or ctx.read_or_none("/run/reboot-required.pkgs") or ""
        pkgs = sorted({p.strip() for p in pkgs_text.splitlines() if p.strip()})
        summary = "a reboot is required to finish applying updates"
        if pkgs:
            shown = ", ".join(pkgs[:3]) + (f" (+{len(pkgs) - 3} more)" if len(pkgs) > 3 else "")
            summary += f" ({shown})"
        return Finding(
            check_id="system.reboot",
            title="Reboot required",
            status=Status.WARNING,
            summary=summary,
            details={"packages": pkgs},
            reason="Updated components such as the kernel or core libraries are not active until the system restarts, "
            "so security fixes may not be in effect yet.",
            recommendation="Plan a reboot at a convenient time (e.g. `sudo reboot`) after checking nothing critical is running.",
            source=flag,
        )
    if "debian" in family or "ubuntu" in family:
        return Finding(
            check_id="system.reboot",
            title="Reboot required",
            status=Status.OK,
            summary="no reboot pending",
            source=flag,
        )
    return Finding(
        check_id="system.reboot",
        title="Reboot required",
        status=Status.UNSUPPORTED,
        summary="reboot-required detection is only implemented for Debian/Ubuntu-based systems",
        limitations=["on Fedora/RHEL, `needs-restarting -r` (dnf-utils) provides this information"],
    )
