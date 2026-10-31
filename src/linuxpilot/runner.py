"""Run checks concurrently and turn crashes into honest UNKNOWN findings."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from linuxpilot.context import Context
from linuxpilot.models import Finding, Status
from linuxpilot.registry import Check


@dataclass
class RunResult:
    findings: list[Finding]
    duration: float


def _run_one(ctx: Context, chk: Check) -> list[Finding]:
    if chk.network and not ctx.network:
        return [
            Finding(
                check_id=chk.id,
                title=chk.title,
                status=Status.SKIPPED,
                summary="skipped (--no-network)",
            )
        ]
    try:
        result = chk.func(ctx)
    except Exception as exc:  # a bug or unexpected system state, not a system problem
        return [
            Finding(
                check_id=chk.id,
                title=chk.title,
                status=Status.UNKNOWN,
                summary="check could not complete",
                limitations=[f"{type(exc).__name__}: {exc}"],
                recommendation="This is likely a LinuxPilot bug; please report it with this output.",
            )
        ]
    return result if isinstance(result, list) else [result]


def run_checks(ctx: Context, checks: list[Check], max_workers: int = 8) -> RunResult:
    """Run checks in parallel; results keep the registry order."""
    start = time.monotonic()
    if not checks:
        return RunResult([], 0.0)
    with ThreadPoolExecutor(max_workers=min(max_workers, len(checks))) as pool:
        futures = [pool.submit(_run_one, ctx, chk) for chk in checks]
        findings: list[Finding] = []
        for fut in futures:
            findings.extend(fut.result())
    return RunResult(findings, time.monotonic() - start)
