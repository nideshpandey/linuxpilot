"""Filesystem space, inodes and read-only mounts."""

from __future__ import annotations

from linuxpilot.checks._util import human_bytes
from linuxpilot.context import Context
from linuxpilot.models import Finding, Status
from linuxpilot.registry import check

# Filesystems that hold real data even without a /dev device path.
_NETWORK_OR_POOL_FS = {"zfs", "nfs", "nfs4", "cifs", "smb3", "fuse.sshfs", "ceph", "glusterfs"}
# Read-only by design or not interesting for capacity.
_IGNORED_FS = {"squashfs", "iso9660", "udf", "erofs", "overlay", "tmpfs", "devtmpfs", "ramfs"}
_IGNORED_PREFIXES = ("/snap/", "/var/lib/docker/", "/var/lib/containers/", "/run/", "/proc", "/sys", "/dev")


def _unescape(path: str) -> str:
    """/proc/mounts escapes spaces as \\040, tabs as \\011, etc."""
    return (path.replace("\\040", " ").replace("\\011", "\t")
            .replace("\\012", "\n").replace("\\134", "\\"))


def parse_mounts(text: str) -> list[dict]:
    """Return real data filesystems from /proc/self/mounts, one entry per device."""
    seen_devices: set[str] = set()
    mounts: list[dict] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        device, mountpoint, fstype, options = parts[0], _unescape(parts[1]), parts[2], parts[3]
        if fstype in _IGNORED_FS:
            continue
        if not (device.startswith("/dev/") or fstype in _NETWORK_OR_POOL_FS):
            continue
        if mountpoint != "/" and mountpoint.startswith(_IGNORED_PREFIXES):
            continue
        key = device if fstype not in ("btrfs", "zfs") else f"{device}:{mountpoint}"
        if key in seen_devices:  # bind mount of something already listed
            continue
        seen_devices.add(key)
        opts = options.split(",")
        mounts.append({
            "device": device,
            "mount": mountpoint,
            "fstype": fstype,
            "read_only": "ro" in opts or "emergency_ro" in opts,
            "emergency_ro": "emergency_ro" in opts,
        })
    return mounts


def parse_fstab(text: str | None) -> dict[str, list[str]]:
    """Map mount point -> options for entries in /etc/fstab."""
    result: dict[str, list[str]] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 4:
            result[_unescape(parts[1])] = parts[3].split(",")
    return result


def _pct(used: int, avail: int) -> float | None:
    denom = used + avail
    return None if denom <= 0 else 100.0 * used / denom


def _level(pct: float | None, warn: float, crit: float) -> Status:
    if pct is None:
        return Status.OK
    if pct >= crit:
        return Status.CRITICAL
    if pct >= warn:
        return Status.WARNING
    return Status.OK


def _check_mount(ctx: Context, m: dict, fstab: dict[str, list[str]]) -> Finding:
    mount = m["mount"]
    # Was read-only requested on purpose? emergency_ro is always an error.
    if m["read_only"] and not m["emergency_ro"]:
        if mount in fstab:
            m["read_only_expected"] = "ro" in fstab[mount]
        else:
            m["read_only_expected"] = None  # mounted by something else; intent unknown
    title = f"Disk {mount}"
    try:
        st = ctx.statvfs(mount)
    except PermissionError:
        return Finding("disk.usage", title, Status.UNKNOWN, "permission denied",
                       details=m, limitations=[f"cannot stat {mount} as this user"])
    except OSError as exc:
        return Finding("disk.usage", title, Status.UNKNOWN, "could not read filesystem usage",
                       details=m, limitations=[f"statvfs({mount}): {exc}"])

    block = st.f_frsize or st.f_bsize
    total = st.f_blocks * block
    used = (st.f_blocks - st.f_bfree) * block
    avail = st.f_bavail * block  # space available to normal users (like df)
    space_pct = _pct(used, avail)

    inode_pct = None
    if st.f_files > 0:  # btrfs/zfs report 0 inodes: not applicable
        inode_used = st.f_files - st.f_ffree
        inode_pct = _pct(inode_used, st.f_favail if st.f_favail else st.f_ffree)

    t = ctx.thresholds
    space_status = _level(space_pct, t["disk_warning_percent"], t["disk_critical_percent"])
    inode_status = _level(inode_pct, t["inode_warning_percent"], t["inode_critical_percent"])

    details = {
        **m,
        "total_bytes": total,
        "used_bytes": used,
        "available_bytes": avail,
        "used_percent": round(space_pct, 1) if space_pct is not None else None,
        "inodes_used_percent": round(inode_pct, 1) if inode_pct is not None else None,
    }
    if total == 0:
        return Finding("disk.usage", title, Status.UNKNOWN, "filesystem reports zero size",
                       details=details, limitations=["statvfs returned 0 blocks"])

    summary = f"{space_pct:.0f}% used ({human_bytes(used)} / {human_bytes(used + avail)}, {human_bytes(avail)} free)"
    reasons, recs = [], []
    status = Status.OK

    def raise_to(s: Status) -> None:
        nonlocal status
        order = [Status.OK, Status.WARNING, Status.CRITICAL]
        if order.index(s) > order.index(status):
            status = s

    unexpected_ro = m["read_only"] and (m["emergency_ro"] or m.get("read_only_expected") is False)
    # A filesystem nobody can write to cannot "fill up", so skip space/inode
    # warnings for intentionally read-only mounts.
    if m["read_only"] and not unexpected_ro:
        space_status = inode_status = Status.OK
        how = "by /etc/fstab" if m.get("read_only_expected") else "not in /etc/fstab, mounted by another tool"
        summary = f"read-only ({how}); " + summary

    if space_status.is_problem:
        raise_to(space_status)
        reasons.append(f"{mount} is {space_pct:.0f}% full. When a filesystem fills up, databases, logs and package "
                       "updates start failing, often all at once.")
        recs.append(f"Find what is using space: `sudo du -xh --max-depth=2 {mount} | sort -h | tail -20`. "
                    "Common culprits: `journalctl --disk-usage`, Docker (`docker system df`), and old logs in /var/log.")
    if inode_status.is_problem:
        raise_to(inode_status)
        summary += f", inodes {inode_pct:.0f}% used"
        reasons.append(f"{inode_pct:.0f}% of file slots (inodes) on {mount} are used. When they run out, no new files "
                       "can be created even if free space remains; usually caused by millions of tiny files.")
        recs.append(f"Find directories with many files: `sudo du -x --inodes {mount} 2>/dev/null | sort -n | tail -20`.")
    if unexpected_ro:
        raise_to(Status.CRITICAL if mount == "/" or m["emergency_ro"] else Status.WARNING)
        summary = "unexpectedly READ-ONLY; " + summary
        why = ("the kernel stopped the filesystem after errors (emergency_ro)" if m["emergency_ro"]
               else "/etc/fstab expects it to be writable")
        reasons.append(f"{mount} is mounted read-only but {why}. The kernel usually does this after disk errors to "
                       "prevent damage; on a Raspberry Pi it is a common sign of a failing SD card.")
        recs.append("Check for storage errors with `sudo dmesg | grep -iE 'error|mmc|ext4|i/o'` and back up important "
                    "data before rebooting.")

    return Finding(
        check_id="disk.usage",
        title=title,
        status=status,
        summary=summary,
        details=details,
        reason=" ".join(reasons) or None,
        recommendation=" ".join(recs) or None,
        source="/proc/self/mounts, statvfs()",
    )


@check("disk.usage", "Disk")
def disk_usage(ctx: Context) -> list[Finding]:
    """Space, inode usage and read-only state for each mounted data filesystem."""
    try:
        text = ctx.read("/proc/self/mounts")
    except OSError:
        text = ctx.read_or_none("/proc/mounts")
        if text is None:
            return [Finding("disk.usage", "Disk", Status.UNKNOWN, "could not read the mount table",
                            limitations=["/proc/self/mounts and /proc/mounts are unreadable"])]
    mounts = parse_mounts(text)
    if not mounts:
        return [Finding("disk.usage", "Disk", Status.UNKNOWN, "no data filesystems found in the mount table",
                        limitations=["this can happen inside some containers"])]
    fstab = parse_fstab(ctx.read_or_none("/etc/fstab"))
    # Root first, then alphabetical.
    mounts.sort(key=lambda m: (m["mount"] != "/", m["mount"]))
    return [_check_mount(ctx, m, fstab) for m in mounts]
