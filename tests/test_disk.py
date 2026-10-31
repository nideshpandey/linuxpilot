from conftest import statvfs

from linuxpilot.checks.disk import disk_usage, parse_mounts
from linuxpilot.models import Status

MOUNTS = """\
/dev/sda2 / ext4 rw,relatime 0 0
proc /proc proc rw 0 0
tmpfs /run tmpfs rw 0 0
/dev/sda1 /boot/efi vfat rw 0 0
/dev/loop3 /snap/core/123 squashfs ro 0 0
overlay /var/lib/docker/overlay2/abc/merged overlay rw 0 0
/dev/sdb1 /mnt/my\\040data ext4 rw 0 0
/dev/sda2 /srv/bind ext4 rw,relatime 0 0
server:/export /mnt/nfs nfs4 rw 0 0
"""


def test_parse_mounts_keeps_only_real_filesystems():
    mounts = {m["mount"] for m in parse_mounts(MOUNTS)}
    assert mounts == {"/", "/boot/efi", "/mnt/my data", "/mnt/nfs"}


def ctx_for(make_ctx, mounts, stats, fstab=""):
    return make_ctx({"/proc/self/mounts": mounts, "/etc/fstab": fstab}, statvfs_map=stats)


def test_disk_ok(make_ctx):
    r = disk_usage(ctx_for(make_ctx, "/dev/sda2 / ext4 rw 0 0\n", {"/": statvfs(used_pct=40)}))
    assert len(r) == 1 and r[0].status is Status.OK
    assert r[0].details["used_percent"] == 40.0


def test_disk_warning_and_critical(make_ctx):
    mounts = "/dev/sda2 / ext4 rw 0 0\n/dev/sdb1 /data ext4 rw 0 0\n"
    r = disk_usage(ctx_for(make_ctx, mounts, {"/": statvfs(used_pct=88), "/data": statvfs(used_pct=97)}))
    by_mount = {x.details["mount"]: x for x in r}
    assert by_mount["/"].status is Status.WARNING
    assert by_mount["/data"].status is Status.CRITICAL
    assert "du -xh" in by_mount["/data"].recommendation
    assert r[0].details["mount"] == "/"  # root is listed first


def test_inode_exhaustion(make_ctx):
    r = disk_usage(ctx_for(make_ctx, "/dev/sda2 / ext4 rw 0 0\n", {"/": statvfs(used_pct=30, inode_used_pct=99)}))
    assert r[0].status is Status.CRITICAL
    assert "inodes" in r[0].summary


def test_btrfs_zero_inodes_is_not_a_problem(make_ctx):
    r = disk_usage(ctx_for(make_ctx, "/dev/sda2 / btrfs rw 0 0\n", {"/": statvfs(used_pct=30, inodes=0)}))
    assert r[0].status is Status.OK
    assert r[0].details["inodes_used_percent"] is None


def test_unexpected_readonly_root_is_critical(make_ctx):
    r = disk_usage(ctx_for(make_ctx, "/dev/mmcblk0p2 / ext4 ro,relatime 0 0\n", {"/": statvfs()},
                           fstab="PARTUUID=abc / ext4 defaults,noatime 0 1\n"))
    assert r[0].status is Status.CRITICAL
    assert "SD card" in r[0].reason


def test_emergency_ro_is_critical_even_if_flagged_rw(make_ctx):
    r = disk_usage(ctx_for(make_ctx, "/dev/sdb1 /data ext4 rw,emergency_ro 0 0\n", {"/data": statvfs()}))
    assert r[0].status is Status.CRITICAL


def test_intentional_readonly_is_ok_and_ignores_fullness(make_ctx):
    r = disk_usage(ctx_for(make_ctx, "/dev/sdb1 /media/archive ext4 ro 0 0\n",
                           {"/media/archive": statvfs(used_pct=99)},
                           fstab="/dev/sdb1 /media/archive ext4 ro,noauto 0 0\n"))
    assert r[0].status is Status.OK
    assert "read-only" in r[0].summary


def test_permission_denied_is_unknown(make_ctx):
    r = disk_usage(ctx_for(make_ctx, "/dev/sdb1 /secret ext4 rw 0 0\n", {"/secret": PermissionError()}))
    assert r[0].status is Status.UNKNOWN


def test_no_mount_table_is_unknown(make_ctx):
    r = disk_usage(make_ctx())
    assert r[0].status is Status.UNKNOWN
