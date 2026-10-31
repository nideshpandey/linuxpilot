"""Default route, interface errors, DNS and internet reachability.

`network.route` and `network.interfaces` only read local files. `network.dns`
and `network.internet` send traffic off the machine and are skipped with
--no-network.
"""

from __future__ import annotations

import socket
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

from linuxpilot.context import Context
from linuxpilot.models import Finding, Status
from linuxpilot.registry import check


def _hex_to_ipv4(h: str) -> str:
    return socket.inet_ntoa(struct.pack("<I", int(h, 16)))


def parse_default_routes(route_text: str | None, ipv6_text: str | None) -> list[dict]:
    routes = []
    if route_text:
        for line in route_text.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 8 and parts[1] == "00000000" and parts[7] == "00000000":
                try:
                    gw = _hex_to_ipv4(parts[2])
                except (ValueError, struct.error, OSError):
                    gw = None
                routes.append({"family": "ipv4", "interface": parts[0], "gateway": gw})
    if ipv6_text:
        for line in ipv6_text.splitlines():
            parts = line.split()
            # dest(32 hex) dest_prefix src src_prefix next_hop metric refcnt use flags iface
            if len(parts) >= 10 and parts[0] == "0" * 32 and parts[1] == "00" and parts[9] != "lo":
                routes.append({"family": "ipv6", "interface": parts[9], "gateway": None})
    return routes


@check("network.route", "Default route")
def default_route(ctx: Context) -> Finding:
    """Whether the system has a default route (a way to reach other networks)."""
    v4 = ctx.read_or_none("/proc/net/route")
    v6 = ctx.read_or_none("/proc/net/ipv6_route")
    if v4 is None and v6 is None:
        return Finding("network.route", "Default route", Status.UNKNOWN, "could not read routing tables",
                       limitations=["/proc/net/route and /proc/net/ipv6_route are unreadable"])
    routes = parse_default_routes(v4, v6)
    if not routes:
        return Finding(
            "network.route", "Default route", Status.WARNING, "no default route",
            reason="Without a default route the machine can only reach its local network, so updates, "
            "DNS lookups and remote services will fail.",
            recommendation="Check the network connection and configuration with `ip addr` and `ip route`.",
            source="/proc/net/route, /proc/net/ipv6_route",
        )
    v4r = [r for r in routes if r["family"] == "ipv4"]
    if v4r:
        summary = f"via {v4r[0]['gateway']} on {v4r[0]['interface']}"
    else:
        summary = f"IPv6 only, on {routes[0]['interface']}"
    if any(r["family"] == "ipv6" for r in routes) and v4r:
        summary += " (+IPv6)"
    return Finding("network.route", "Default route", Status.OK, summary, details={"routes": routes},
                   source="/proc/net/route, /proc/net/ipv6_route")


def parse_net_dev(text: str) -> dict[str, dict[str, int]]:
    stats: dict[str, dict[str, int]] = {}
    for line in text.splitlines()[2:]:
        if ":" not in line:
            continue
        name, _, rest = line.partition(":")
        f = rest.split()
        if len(f) < 16:
            continue
        try:
            nums = [int(x) for x in f[:16]]
        except ValueError:
            continue
        stats[name.strip()] = {
            "rx_bytes": nums[0], "rx_packets": nums[1], "rx_errors": nums[2], "rx_dropped": nums[3],
            "tx_bytes": nums[8], "tx_packets": nums[9], "tx_errors": nums[10], "tx_dropped": nums[11],
        }
    return stats


@check("network.interfaces", "Interfaces")
def interfaces(ctx: Context) -> Finding:
    """Interfaces that are up, and their error counts since boot."""
    text = ctx.read_or_none("/proc/net/dev")
    if text is None:
        return Finding("network.interfaces", "Interfaces", Status.UNKNOWN, "could not read /proc/net/dev",
                       limitations=["/proc/net/dev unreadable"])
    stats = parse_net_dev(text)
    up, noisy = [], []
    for name, s in stats.items():
        if name == "lo":
            continue
        state = (ctx.read_or_none(f"/sys/class/net/{name}/operstate") or "unknown").strip()
        if state not in ("up", "unknown"):
            continue
        # Skip virtual bridges/veths created by Docker etc. for the summary.
        if name.startswith(("veth", "docker", "br-", "virbr")):
            continue
        up.append(name)
        packets = s["rx_packets"] + s["tx_packets"]
        errors = s["rx_errors"] + s["tx_errors"]
        if packets >= 1000 and errors / packets >= 0.01:
            noisy.append({"interface": name, "errors": errors, "packets": packets,
                          "error_percent": round(100.0 * errors / packets, 2)})
    if not up:
        return Finding("network.interfaces", "Interfaces", Status.WARNING, "no network interface is up",
                       details={"interfaces": stats},
                       reason="No physical or wireless interface reports a link, so the machine is offline.",
                       recommendation="Check cables or Wi-Fi, then `ip link` to see interface states.",
                       source="/proc/net/dev, /sys/class/net/*/operstate")
    if noisy:
        worst = max(noisy, key=lambda n: n["error_percent"])
        return Finding(
            "network.interfaces", "Interfaces", Status.WARNING,
            f"{worst['interface']} has {worst['error_percent']}% packet errors since boot",
            details={"up": up, "noisy": noisy, "interfaces": stats},
            reason="A high rate of packet errors usually points to a bad cable, a failing port, Wi-Fi interference "
            "or a duplex mismatch, and causes slow or unreliable connections.",
            recommendation=f"Inspect counters with `ip -s link show {worst['interface']}`; try another cable or port.",
            source="/proc/net/dev",
        )
    return Finding("network.interfaces", "Interfaces", Status.OK, f"up: {', '.join(up)}",
                   details={"up": up, "interfaces": stats}, source="/proc/net/dev")


def _with_timeout(func, timeout: float):
    """Run a blocking call (e.g. getaddrinfo, which has no timeout) with a deadline."""
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(func)
    try:
        return future.result(timeout=timeout)
    finally:
        pool.shutdown(wait=False)


def _nameservers(ctx: Context) -> list[str]:
    text = ctx.read_or_none("/etc/resolv.conf") or ""
    return [line.split()[1] for line in text.splitlines()
            if line.strip().startswith("nameserver") and len(line.split()) > 1]


@check("network.dns", "DNS", network=True)
def dns(ctx: Context) -> Finding:
    """Whether a public hostname can be resolved."""
    name = ctx.dns_test_name
    servers = _nameservers(ctx)
    start = time.monotonic()
    try:
        addrs = _with_timeout(lambda: socket.getaddrinfo(name, 443, proto=socket.IPPROTO_TCP), ctx.network_timeout)
    except FutureTimeout:
        error = f"lookup of {name} timed out after {ctx.network_timeout:g}s"
    except socket.gaierror as exc:
        error = f"lookup of {name} failed ({exc.strerror or exc})"
    else:
        ms = (time.monotonic() - start) * 1000
        return Finding("network.dns", "DNS", Status.OK, f"resolved {name} in {ms:.0f} ms",
                       details={"name": name, "addresses": sorted({a[4][0] for a in addrs}),
                                "nameservers": servers, "milliseconds": round(ms)},
                       source="getaddrinfo(), /etc/resolv.conf")
    return Finding(
        "network.dns", "DNS", Status.WARNING, error,
        details={"name": name, "nameservers": servers},
        reason="Without working DNS, almost everything that uses hostnames fails: updates, APIs, email, "
        "even if the network itself is up.",
        recommendation="Check configured servers (`cat /etc/resolv.conf`, or `resolvectl status` on systemd-resolved) "
        "and test one directly, e.g. `nslookup example.com 1.1.1.1`.",
        source="getaddrinfo(), /etc/resolv.conf",
        limitations=["a failure can also mean this network blocks outbound DNS"],
    )


def _tcp_connect(host: str, port: int, timeout: float) -> float:
    start = time.monotonic()
    with socket.create_connection((host, port), timeout=timeout):
        pass
    return (time.monotonic() - start) * 1000


@check("network.internet", "Internet", network=True)
def internet(ctx: Context) -> Finding:
    """Whether well-known public addresses are reachable over TCP (no DNS needed)."""
    errors = []
    for host, port in ctx.connectivity_targets:
        try:
            ms = _tcp_connect(host, port, ctx.network_timeout)
        except OSError as exc:
            errors.append(f"{host}:{port} {exc.strerror or exc}")
            continue
        return Finding("network.internet", "Internet", Status.OK, f"reachable ({host} in {ms:.0f} ms)",
                       details={"target": f"{host}:{port}", "milliseconds": round(ms)},
                       source="TCP connect")
    return Finding(
        "network.internet", "Internet", Status.WARNING, "public internet is not reachable",
        details={"errors": errors},
        reason="TCP connections to well-known public addresses failed, so the machine is probably offline "
        "or behind a firewall that blocks outbound traffic.",
        recommendation="Check the default route (`ip route`), then whether the router/gateway is reachable "
        "with `ping -c 3 <gateway>`.",
        source="TCP connect",
        limitations=["networks that only allow traffic through a proxy will also show this"],
    )
