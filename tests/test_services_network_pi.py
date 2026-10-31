import socket

from conftest import mkdir

from linuxpilot.checks import network as net
from linuxpilot.checks.pi import parse_throttled, pi_power
from linuxpilot.checks.services import failed_services
from linuxpilot.checks.system import reboot_required, system_info
from linuxpilot.context import CommandNotFound, CommandResult, CommandTimeout
from linuxpilot.models import Status

# ------------------------------------------------------------------ systemd


def test_no_systemd_is_unsupported(make_ctx):
    assert failed_services(make_ctx()).status is Status.UNSUPPORTED


def systemd_ctx(make_ctx, commands):
    ctx = make_ctx(commands=commands)
    mkdir(ctx, "/run/systemd/system")
    return ctx


def test_no_failed_units(make_ctx):
    ctx = systemd_ctx(make_ctx, {"systemctl list-units": ""})
    assert failed_services(ctx).status is Status.OK


def test_failed_units_explain_result(make_ctx):
    ctx = systemd_ctx(make_ctx, {
        "systemctl list-units": "nginx.service loaded failed failed A high performance web server\n"
                                "● backup.timer loaded failed failed Nightly backup\n",
        "systemctl show": "Id=nginx.service\nResult=oom-kill\n\nId=backup.timer\nResult=exit-code\n",
    })
    r = failed_services(ctx)
    assert r.status is Status.WARNING
    assert r.details["units"] == ["nginx.service", "backup.timer"]
    assert "too much memory" in r.summary
    assert "journalctl -u nginx.service" in r.recommendation


def test_systemctl_missing_or_hanging_is_unknown(make_ctx):
    assert failed_services(systemd_ctx(make_ctx, {"systemctl": CommandNotFound("systemctl")})).status is Status.UNKNOWN
    assert failed_services(systemd_ctx(make_ctx, {"systemctl": CommandTimeout("x")})).status is Status.UNKNOWN
    bad = systemd_ctx(make_ctx, {"systemctl": CommandResult(1, "", "Failed to connect to bus")})
    r = failed_services(bad)
    assert r.status is Status.UNKNOWN and "bus" in r.limitations[0]


# ------------------------------------------------------------------ network

ROUTE = """Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT
eth0\t00000000\t0101A8C0\t0003\t0\t0\t100\t00000000\t0\t0\t0
eth0\t0001A8C0\t00000000\t0001\t0\t0\t100\t00FFFFFF\t0\t0\t0
"""


def test_default_route_parsed(make_ctx):
    r = net.default_route(make_ctx({"/proc/net/route": ROUTE}))
    assert r.status is Status.OK
    assert "192.168.1.1" in r.summary and "eth0" in r.summary


def test_no_default_route_warns(make_ctx):
    only_local = ROUTE.splitlines()[0] + "\n" + ROUTE.splitlines()[2] + "\n"
    r = net.default_route(make_ctx({"/proc/net/route": only_local, "/proc/net/ipv6_route": ""}))
    assert r.status is Status.WARNING


NET_DEV = """Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo: 100 10 0 0 0 0 0 0 100 10 0 0 0 0 0 0
  eth0: 9000000 {rxp} {rxe} 0 0 0 0 0 8000000 50000 0 0 0 0 0 0
"""


def test_interfaces_ok_and_noisy(make_ctx):
    ok = make_ctx({"/proc/net/dev": NET_DEV.format(rxp=50000, rxe=0), "/sys/class/net/eth0/operstate": "up\n"})
    assert net.interfaces(ok).status is Status.OK
    noisy = make_ctx({"/proc/net/dev": NET_DEV.format(rxp=50000, rxe=5000), "/sys/class/net/eth0/operstate": "up"})
    r = net.interfaces(noisy)
    assert r.status is Status.WARNING and "eth0" in r.summary


def test_interfaces_all_down(make_ctx):
    ctx = make_ctx({"/proc/net/dev": NET_DEV.format(rxp=1, rxe=0), "/sys/class/net/eth0/operstate": "down"})
    assert net.interfaces(ctx).status is Status.WARNING


def test_dns_success_and_failure(make_ctx, monkeypatch):
    monkeypatch.setattr(net.socket, "getaddrinfo",
                        lambda *a, **k: [(socket.AF_INET, 1, 6, "", ("93.184.215.14", 443))])
    assert net.dns(make_ctx({"/etc/resolv.conf": "nameserver 1.1.1.1\n"})).status is Status.OK

    def fail(*a, **k):
        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(net.socket, "getaddrinfo", fail)
    r = net.dns(make_ctx())
    assert r.status is Status.WARNING and "failed" in r.summary


def test_internet_unreachable(make_ctx, monkeypatch):
    def refuse(*a, **k):
        raise OSError(101, "Network is unreachable")

    monkeypatch.setattr(net.socket, "create_connection", refuse)
    r = net.internet(make_ctx())
    assert r.status is Status.WARNING
    assert len(r.details["errors"]) == 2


# ------------------------------------------------------------------ raspberry pi

PI_MODEL = "Raspberry Pi 4 Model B Rev 1.4\x00"
SYSFS = "/sys/devices/platform/soc/soc:firmware/get_throttled"


def test_parse_throttled_formats():
    assert parse_throttled("throttled=0x50000\n") == 0x50000
    assert parse_throttled("0x0") == 0
    assert parse_throttled("50005") == 0x50005
    assert parse_throttled("nonsense") is None


def test_not_a_pi(make_ctx):
    assert pi_power(make_ctx({"/proc/device-tree/model": "Generic x86 board"})).status is Status.UNSUPPORTED


def test_pi_healthy_from_sysfs(make_ctx):
    r = pi_power(make_ctx({"/proc/device-tree/model": PI_MODEL, SYSFS: "0\n"}))
    assert r.status is Status.OK


def test_pi_undervoltage_history_is_warning(make_ctx):
    r = pi_power(make_ctx({"/proc/device-tree/model": PI_MODEL, SYSFS: "50000\n"}))
    assert r.status is Status.WARNING and "since boot" in r.summary


def test_pi_undervoltage_now_is_critical_via_vcgencmd(make_ctx):
    ctx = make_ctx({"/proc/device-tree/model": PI_MODEL}, commands={"vcgencmd": "throttled=0x50005\n"})
    r = pi_power(ctx)
    assert r.status is Status.CRITICAL
    assert "under-voltage now" in r.details["flags"]


def test_pi_without_vcgencmd_is_unknown_with_hint(make_ctx):
    r = pi_power(make_ctx({"/proc/device-tree/model": PI_MODEL}))
    assert r.status is Status.UNKNOWN
    assert any("vcgencmd" in note for note in r.limitations)


def test_pi_vcgencmd_permission_hint(make_ctx):
    ctx = make_ctx({"/proc/device-tree/model": PI_MODEL},
                   commands={"vcgencmd": CommandResult(255, "", "VCHI initialization failed")})
    r = pi_power(ctx)
    assert r.status is Status.UNKNOWN
    assert any("video" in note for note in r.limitations)


# ------------------------------------------------------------------ system


def test_system_info(make_ctx):
    ctx = make_ctx({
        "/etc/os-release": 'NAME="Debian GNU/Linux"\nPRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\nID=debian\n',
        "/proc/sys/kernel/osrelease": "6.1.0-18-amd64\n",
        "/proc/uptime": "93784.12 180000.00\n",
    })
    r = system_info(ctx)
    assert r.status is Status.OK
    assert r.details["os"] == "Debian GNU/Linux 12 (bookworm)"
    assert r.details["uptime_human"] == "1 day, 2h"


def test_system_info_tolerates_missing_files(make_ctx):
    r = system_info(make_ctx())
    assert r.status is Status.OK and r.limitations


DEBIAN = {"/etc/os-release": "ID=ubuntu\nID_LIKE=debian\n"}


def test_reboot_not_required(make_ctx):
    assert reboot_required(make_ctx(DEBIAN)).status is Status.OK


def test_reboot_required(make_ctx):
    r = reboot_required(make_ctx({**DEBIAN, "/var/run/reboot-required": "",
                                  "/var/run/reboot-required.pkgs": "linux-image-6.8\nlibc6\n"}))
    assert r.status is Status.WARNING and "libc6" in r.summary


def test_reboot_detection_unsupported_elsewhere(make_ctx):
    assert reboot_required(make_ctx({"/etc/os-release": "ID=fedora\n"})).status is Status.UNSUPPORTED
