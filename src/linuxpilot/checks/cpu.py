"""CPU load, a short usage sample, and temperature."""

from __future__ import annotations

import os
import time

from linuxpilot.context import Context
from linuxpilot.models import Finding, Status
from linuxpilot.registry import check

# ---------------------------------------------------------------- load


@check("cpu.load", "CPU load")
def cpu_load(ctx: Context) -> Finding:
    """Load averages compared with the number of CPU cores."""
    try:
        text = ctx.read("/proc/loadavg")
        load1, load5, load15 = (float(x) for x in text.split()[:3])
    except (OSError, ValueError) as exc:
        return Finding("cpu.load", "CPU load", Status.UNKNOWN, "could not read load average",
                       limitations=[f"/proc/loadavg: {exc}"])

    cores = ctx.cpu_count()
    per_core = load5 / cores
    t = ctx.thresholds
    if per_core >= t["load_per_core_critical"]:
        status = Status.CRITICAL
    elif per_core >= t["load_per_core_warning"]:
        status = Status.WARNING
    else:
        status = Status.OK

    finding = Finding(
        check_id="cpu.load",
        title="CPU load",
        status=status,
        summary=f"{load1:.2f} / {load5:.2f} / {load15:.2f} (1/5/15 min) on {cores} core{'s' if cores != 1 else ''}",
        details={"load1": load1, "load5": load5, "load15": load15, "cores": cores,
                 "load5_per_core": round(per_core, 2)},
        source="/proc/loadavg",
    )
    if status.is_problem:
        finding.reason = (
            f"Over the last 5 minutes there were on average {per_core:.1f} runnable or waiting tasks per core. "
            "Sustained load above the core count means work is queueing; on Linux this also counts tasks "
            "waiting on slow disks or network storage."
        )
        finding.recommendation = (
            "Run `top` (press P to sort by CPU) to find busy processes; if CPU usage is low but load is high, "
            "check disk I/O with `vmstat 1` (the 'wa' and 'b' columns)."
        )
    return finding


# ---------------------------------------------------------------- usage sample


def _read_cpu_times(ctx: Context) -> list[int]:
    for line in ctx.read("/proc/stat").splitlines():
        if line.startswith("cpu "):
            return [int(x) for x in line.split()[1:]]
    raise ValueError("no aggregate 'cpu' line in /proc/stat")


def _usage(before: list[int], after: list[int]) -> dict[str, float]:
    # Fields: user nice system idle iowait irq softirq steal guest guest_nice
    n = min(len(before), len(after))
    delta = [after[i] - before[i] for i in range(n)]
    # guest/guest_nice are already included in user/nice.
    total = sum(delta[:8]) if n >= 8 else sum(delta)
    if total <= 0:
        raise ValueError("no CPU time elapsed between samples")

    def pct(i: int) -> float:
        return 100.0 * delta[i] / total if i < n else 0.0

    idle = pct(3)
    iowait = pct(4)
    steal = pct(7)
    return {
        "busy_percent": round(100.0 - idle - iowait, 1),
        "iowait_percent": round(iowait, 1),
        "steal_percent": round(steal, 1),
        "idle_percent": round(idle, 1),
    }


@check("cpu.usage", "CPU usage")
def cpu_usage(ctx: Context) -> Finding:
    """Short CPU usage sample, including I/O wait and VM steal time."""
    if ctx.quick:
        return Finding("cpu.usage", "CPU usage", Status.SKIPPED, "skipped (--quick)")
    try:
        before = _read_cpu_times(ctx)
        time.sleep(ctx.sample_seconds)
        after = _read_cpu_times(ctx)
        u = _usage(before, after)
    except (OSError, ValueError) as exc:
        return Finding("cpu.usage", "CPU usage", Status.UNKNOWN, "could not sample CPU usage",
                       limitations=[f"/proc/stat: {exc}"])

    t = ctx.thresholds
    status = Status.OK
    reasons, recs = [], []
    if u["iowait_percent"] >= t["iowait_warning_percent"]:
        status = Status.WARNING
        reasons.append(f"The CPU spent {u['iowait_percent']:.0f}% of the sample waiting for disk or network I/O, "
                       "which usually means storage is the bottleneck.")
        recs.append("Check which processes are doing I/O with `sudo iotop -o` or `pidstat -d 1`, "
                    "and look for disk errors with `sudo dmesg | tail -50`.")
    if u["steal_percent"] >= t["steal_warning_percent"]:
        status = Status.WARNING
        reasons.append(f"{u['steal_percent']:.0f}% of CPU time was taken by the hypervisor for other virtual machines "
                       "(steal time), so this VM is getting less CPU than it is allocated.")
        recs.append("If this persists, contact your hosting provider or move to a plan with dedicated CPU.")

    summary = f"{u['busy_percent']:.0f}% busy"
    extras = []
    if u["iowait_percent"] >= 1:
        extras.append(f"{u['iowait_percent']:.0f}% I/O wait")
    if u["steal_percent"] >= 1:
        extras.append(f"{u['steal_percent']:.0f}% steal")
    if extras:
        summary += " (" + ", ".join(extras) + ")"
    summary += f" over {ctx.sample_seconds:g}s"

    return Finding(
        check_id="cpu.usage",
        title="CPU usage",
        status=status,
        summary=summary,
        details={**u, "sample_seconds": ctx.sample_seconds},
        reason=" ".join(reasons) or None,
        recommendation=" ".join(recs) or None,
        source="/proc/stat (two samples)",
        limitations=["a momentary sample; short spikes are normal and are not treated as a problem"],
    )


# ---------------------------------------------------------------- temperature

# Sensor names that measure the CPU/SoC itself, in rough order of preference.
_CPU_HWMON_NAMES = ("coretemp", "k10temp", "zenpower", "cpu_thermal", "soc_thermal", "cpu-thermal", "rpi_cpu")
_CPU_ZONE_TYPES = ("x86_pkg_temp", "cpu-thermal", "cpu_thermal", "soc_thermal", "cpu", "soc-thermal")
_PLAUSIBLE = (-40.0, 150.0)


def _read_millideg(ctx: Context, p: str) -> float | None:
    text = ctx.read_or_none(p)
    if text is None:
        return None
    try:
        value = int(text.strip()) / 1000.0
    except ValueError:
        return None
    if not (_PLAUSIBLE[0] <= value <= _PLAUSIBLE[1]):
        return None
    return value


def collect_sensors(ctx: Context) -> list[dict]:
    """Return every readable temperature sensor as {name, label, celsius, source, is_cpu}."""
    sensors: list[dict] = []

    for hw in ctx.glob("/sys/class/hwmon/hwmon*"):
        name = (ctx.read_or_none(f"{hw}/name") or "").strip()
        for inp in ctx.glob(f"{hw}/temp*_input"):
            celsius = _read_millideg(ctx, inp)
            if celsius is None:
                continue
            base = inp[: -len("_input")]
            label = (ctx.read_or_none(f"{base}_label") or "").strip() or os.path.basename(base)
            sensors.append({
                "name": name or os.path.basename(hw),
                "label": label,
                "celsius": round(celsius, 1),
                "source": inp,
                "is_cpu": name in _CPU_HWMON_NAMES,
            })

    for zone in ctx.glob("/sys/class/thermal/thermal_zone*"):
        ztype = (ctx.read_or_none(f"{zone}/type") or "").strip()
        celsius = _read_millideg(ctx, f"{zone}/temp")
        if celsius is None:
            continue
        sensors.append({
            "name": ztype or os.path.basename(zone),
            "label": os.path.basename(zone),
            "celsius": round(celsius, 1),
            "source": f"{zone}/temp",
            "is_cpu": ztype in _CPU_ZONE_TYPES,
        })
    return sensors


@check("cpu.temperature", "CPU temperature")
def cpu_temperature(ctx: Context) -> Finding:
    """CPU temperature from hwmon / thermal zones, when the hardware exposes it."""
    sensors = collect_sensors(ctx)
    if not sensors:
        return Finding(
            check_id="cpu.temperature",
            title="CPU temperature",
            status=Status.UNSUPPORTED,
            summary="no temperature sensor exposed (normal on most VMs and cloud servers)",
            limitations=["no readable sensors under /sys/class/hwmon or /sys/class/thermal"],
        )

    cpu = [s for s in sensors if s["is_cpu"]]
    limitations = []
    if cpu:
        hottest = max(cpu, key=lambda s: s["celsius"])
    else:
        # Fall back to ACPI or unnamed zones, but say we are unsure.
        hottest = max(sensors, key=lambda s: s["celsius"])
        limitations.append(f"no sensor is clearly labelled as the CPU; using the hottest sensor ({hottest['name']})")

    temp = hottest["celsius"]
    t = ctx.thresholds
    if temp >= t["temperature_critical_c"]:
        status = Status.CRITICAL
    elif temp >= t["temperature_warning_c"]:
        status = Status.WARNING
    else:
        status = Status.OK

    others = [s for s in sensors if s is not hottest and not s["is_cpu"]]
    summary = f"{temp:.0f}°C ({hottest['name']})"
    if others:
        summary += f"; {len(others)} other sensor{'s' if len(others) != 1 else ''} up to " \
                   f"{max(s['celsius'] for s in others):.0f}°C"

    finding = Finding(
        check_id="cpu.temperature",
        title="CPU temperature",
        status=status,
        summary=summary,
        details={"celsius": temp, "sensor": hottest["name"], "sensors": sensors,
                 "other_sensors": [f"{s['name']} {s['celsius']}°C" for s in others]},
        source=hottest["source"],
        limitations=limitations,
    )
    if status.is_problem:
        finding.reason = (
            f"At {temp:.0f}°C most CPUs start reducing their speed to protect themselves (thermal throttling), "
            "which makes the system slower, and sustained heat shortens hardware life."
        )
        finding.recommendation = (
            "Check airflow and fans, clean dust, and find heavy processes with `top`. "
            "On a Raspberry Pi, a heatsink or fan case usually solves this."
        )
    return finding
