"""Core data model: the structured result every check returns."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Status(str, Enum):
    """Result status of a check.

    OK / WARNING / CRITICAL describe the *system*.
    UNKNOWN means LinuxPilot could not inspect the system (e.g. permission denied),
    UNSUPPORTED means the check does not apply here (e.g. no systemd, not a Pi),
    SKIPPED means the user asked not to run it (e.g. --no-network, --quick).
    """

    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"
    SKIPPED = "skipped"

    @property
    def is_problem(self) -> bool:
        return self in (Status.WARNING, Status.CRITICAL)

    @property
    def is_applicable(self) -> bool:
        """False for checks that did not apply or were not run."""
        return self not in (Status.UNSUPPORTED, Status.SKIPPED)


# Higher number = more urgent. Used for sorting and for the overall status.
SEVERITY = {
    Status.CRITICAL: 4,
    Status.WARNING: 3,
    Status.UNKNOWN: 2,
    Status.OK: 1,
    Status.UNSUPPORTED: 0,
    Status.SKIPPED: 0,
}


@dataclass
class Finding:
    """One observation about the system, with an explanation.

    Checks build these; the CLI only renders them. A missing measurement must
    never be reported as zero: use Status.UNKNOWN and add a limitation instead.
    """

    check_id: str
    title: str
    status: Status
    summary: str
    details: dict[str, Any] = field(default_factory=dict)
    reason: str | None = None  # why this matters
    recommendation: str | None = None  # safe next step
    source: str | None = None  # where the evidence came from
    limitations: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "title": self.title,
            "status": self.status.value,
            "summary": self.summary,
            "details": self.details,
            "reason": self.reason,
            "recommendation": self.recommendation,
            "source": self.source,
            "limitations": self.limitations,
            "timestamp": round(self.timestamp, 3),
        }


def worst_status(findings: list[Finding]) -> Status:
    """Overall status: the most severe status among applicable findings."""
    applicable = [f.status for f in findings if f.status.is_applicable]
    if not applicable:
        return Status.UNKNOWN
    return max(applicable, key=lambda s: SEVERITY[s])
