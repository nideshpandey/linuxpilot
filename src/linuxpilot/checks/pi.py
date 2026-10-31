"""Raspberry Pi power and throttling state."""

from __future__ import annotations

from linuxpilot.context import CommandNotFound, CommandTimeout, Context
from linuxpilot.models import Finding, Status
from linuxpilot.registry import check

# Bits of the firmware "get_throttled" value.
BITS = {
    0: "under-voltage now",
    1: "CPU frequency capped now",
    2: "throttled now",
    3: "soft temperature limit active now",
    16: "under-voltage has occurred since boot",
    17: "frequency capping has occurred since boot",
    18: "throttling has occurred since boot",
    19: "soft temperature limit has occurred since boot",
}

_SYSFS_PATHS = (
    "/sys/devices/platform/soc/soc:firmware/get_throttled",
    "/sys/devices/platform/soc/soc:firmware/raspberrypi-hwmon/get_throttled",
)


def pi_model(ctx: Context) -> str | None:
    model = (ctx.read_or_none("/proc/device-tree/model") or "").replace("\x00", "").strip()
    return model if "raspberry pi" in model.lower() else None


def parse_throttled(text: str) -> int | None:
    """Accept '0x50000', 'throttled=0x50000' or '50000'."""
    value = text.strip().split("=")[-1].strip()
    try:
        return int(value, 16)
    except ValueError:
        return None


def _read_throttled(ctx: Context) -> tuple[int | None, str | None, list[str]]:
    notes = []
    for p in _SYSFS_PATHS:
        text = ctx.read_or_none(p)
        if text is not None:
            value = parse_throttled(text)
            if value is not None:
                return value, p, notes
    try:
        res = ctx.run(["vcgencmd", "get_throttled"], timeout=3)
    except CommandNotFound:
        notes.append("vcgencmd is not installed (package: libraspberrypi-bin or raspi-utils)")
        return None, None, notes
    except CommandTimeout:
        notes.append("vcgencmd timed out")
        return None, None, notes
    if res.returncode != 0:
        msg = (res.stderr or res.stdout).strip()
        notes.append(f"vcgencmd failed: {msg[:150]}" if msg else "vcgencmd failed")
        if "permission" in msg.lower() or "vchi" in msg.lower():
            notes.append("add your user to the 'video' group: `sudo usermod -aG video $USER` (then log in again)")
        return None, None, notes
    return parse_throttled(res.stdout), "vcgencmd get_throttled", notes


@check("pi.power", "Pi power & throttling")
def pi_power(ctx: Context) -> Finding:
    """Raspberry Pi under-voltage and throttling, now and since boot."""
    model = pi_model(ctx)
    if model is None:
        return Finding("pi.power", "Pi power & throttling", Status.UNSUPPORTED, "not a Raspberry Pi")

    value, source, notes = _read_throttled(ctx)
    if value is None:
        return Finding("pi.power", "Pi power & throttling", Status.UNKNOWN,
                       "could not read the firmware throttling state",
                       details={"model": model}, limitations=notes or ["throttle state not available"])

    active = [desc for bit, desc in BITS.items() if value & (1 << bit)]
    details = {"model": model, "throttled_hex": hex(value), "flags": active}

    if value & 0x1:  # under-voltage right now
        return Finding(
            "pi.power", "Pi power & throttling", Status.CRITICAL, "UNDER-VOLTAGE right now",
            details=details,
            reason="The Pi is not getting enough power at this moment. This causes random crashes, USB devices "
            "disconnecting, and SD card corruption.",
            recommendation="Use the official power supply for your model (or a good 5.1 V supply with a short, thick "
            "cable) and power hungry USB devices from a powered hub.",
            source=source,
        )
    if value & 0xE:  # capped / throttled / soft limit right now
        return Finding(
            "pi.power", "Pi power & throttling", Status.WARNING, "CPU is throttled right now",
            details=details,
            reason="The CPU is running slower than normal, usually because it is too hot or was recently under-volted.",
            recommendation="Check the temperature (`vcgencmd measure_temp`) and add a heatsink or fan if it is above ~80°C.",
            source=source,
        )
    if value & 0x10000:
        return Finding(
            "pi.power", "Pi power & throttling", Status.WARNING, "under-voltage has occurred since boot",
            details=details,
            reason="Power dipped too low at least once since the last boot. Brief dips under load are a common cause "
            "of unexplained reboots and SD card corruption, even if everything looks fine now.",
            recommendation="Use an adequate power supply and cable; avoid powering drives directly from the Pi's USB ports.",
            source=source,
        )
    if value & 0xE0000:
        return Finding(
            "pi.power", "Pi power & throttling", Status.WARNING, "throttling has occurred since boot",
            details=details,
            reason="The CPU slowed itself down at some point since boot, normally because of heat.",
            recommendation="Improve cooling if this happens under normal workloads.",
            source=source,
        )
    return Finding("pi.power", "Pi power & throttling", Status.OK,
                   f"no under-voltage or throttling since boot ({model})", details=details, source=source)
