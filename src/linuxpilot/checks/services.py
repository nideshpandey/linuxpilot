"""Failed systemd units."""

from __future__ import annotations

from linuxpilot.context import CommandNotFound, CommandTimeout, Context
from linuxpilot.models import Finding, Status
from linuxpilot.registry import check

# Human explanation for systemd's "Result=" values.
_RESULT_HINTS = {
    "exit-code": "exited with an error",
    "signal": "was killed by a signal",
    "core-dump": "crashed (core dump)",
    "timeout": "timed out",
    "oom-kill": "was killed for using too much memory",
    "watchdog": "stopped responding (watchdog)",
    "start-limit-hit": "kept failing and hit its restart limit",
    "resources": "could not get required resources",
    "protocol": "violated the service protocol",
}


def parse_failed_units(output: str) -> list[str]:
    units = []
    for line in output.splitlines():
        parts = line.split()
        if parts and not parts[0].startswith("●"):
            units.append(parts[0])
        elif len(parts) > 1:  # some versions prefix with a bullet
            units.append(parts[1])
    return units


def parse_show(output: str) -> dict[str, dict[str, str]]:
    """Parse `systemctl show -p Id,Result,...` output (blank-line separated blocks)."""
    units: dict[str, dict[str, str]] = {}
    current: dict[str, str] = {}
    for line in output.splitlines() + [""]:
        if not line.strip():
            if current.get("Id"):
                units[current["Id"]] = current
            current = {}
            continue
        key, _, value = line.partition("=")
        current[key] = value
    return units


@check("services.failed", "Failed services")
def failed_services(ctx: Context) -> Finding:
    """systemd units in the failed state."""
    if not ctx.exists("/run/systemd/system"):
        return Finding("services.failed", "Failed services", Status.UNSUPPORTED,
                       "systemd is not the init system here (common in containers, Alpine, Devuan)")
    try:
        res = ctx.run(["systemctl", "list-units", "--state=failed", "--no-legend", "--plain", "--no-pager"])
    except CommandNotFound:
        return Finding("services.failed", "Failed services", Status.UNKNOWN, "systemctl not found",
                       limitations=["systemd appears active but systemctl is not on PATH"])
    except CommandTimeout:
        return Finding("services.failed", "Failed services", Status.UNKNOWN, "systemctl did not respond in time",
                       limitations=["systemctl list-units timed out; systemd may be overloaded"])
    if res.returncode != 0:
        return Finding("services.failed", "Failed services", Status.UNKNOWN, "systemctl returned an error",
                       limitations=[(res.stderr or res.stdout).strip()[:200] or f"exit code {res.returncode}"])

    units = parse_failed_units(res.stdout)
    if not units:
        return Finding("services.failed", "Failed services", Status.OK, "no failed units",
                       source="systemctl list-units --state=failed")

    info: dict[str, dict[str, str]] = {}
    try:
        show = ctx.run(["systemctl", "show", "--no-pager", "-p", "Id,Result,InactiveEnterTimestamp"] + units[:10])
        if show.returncode == 0:
            info = parse_show(show.stdout)
    except (CommandNotFound, CommandTimeout):
        pass

    described = []
    for u in units:
        result = info.get(u, {}).get("Result", "")
        hint = _RESULT_HINTS.get(result)
        described.append(f"{u} ({hint})" if hint else u)

    shown = ", ".join(described[:3]) + (f" (+{len(units) - 3} more)" if len(units) > 3 else "")
    first = units[0]
    return Finding(
        check_id="services.failed",
        title="Failed services",
        status=Status.WARNING,
        summary=f"{len(units)} failed: {shown}",
        details={"units": units, "unit_info": info},
        reason="A failed unit is a service, mount or timer that stopped with an error and is not running. "
        "If it provides something you rely on (web server, database, backup timer), that function is down.",
        recommendation=f"See why: `systemctl status {first}` and `journalctl -u {first} -b --no-pager -n 50`. "
        "If it is no longer needed, `sudo systemctl reset-failed` clears the state.",
        source="systemctl list-units --state=failed",
    )
