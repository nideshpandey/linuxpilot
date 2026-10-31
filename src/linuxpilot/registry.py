"""Check registry. A check is a function decorated with @check(...)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Union

from linuxpilot.context import Context
from linuxpilot.models import Finding

CheckResult = Union[Finding, "list[Finding]"]
CheckFunc = Callable[[Context], CheckResult]


@dataclass(frozen=True)
class Check:
    id: str  # stable identifier, e.g. "cpu.temperature"
    title: str
    category: str  # e.g. "cpu"; used by --only / --skip
    func: CheckFunc
    network: bool = False  # True if the check sends traffic off the machine
    description: str = ""


_REGISTRY: list[Check] = []


def check(
    check_id: str,
    title: str,
    *,
    network: bool = False,
    description: str = "",
) -> Callable[[CheckFunc], CheckFunc]:
    """Register a check function. The category is the part before the first dot."""

    def decorator(func: CheckFunc) -> CheckFunc:
        if any(c.id == check_id for c in _REGISTRY):
            raise ValueError(f"duplicate check id: {check_id}")
        doc_lines = (func.__doc__ or "").strip().splitlines()
        _REGISTRY.append(
            Check(
                id=check_id,
                title=title,
                category=check_id.split(".", 1)[0],
                func=func,
                network=network,
                description=description or (doc_lines[0] if doc_lines else ""),
            )
        )
        return func

    return decorator


def all_checks() -> list[Check]:
    # Importing the package registers every built-in check.
    import linuxpilot.checks  # noqa: F401

    return list(_REGISTRY)


def select_checks(
    checks: list[Check],
    only: list[str] | None = None,
    skip: list[str] | None = None,
) -> list[Check]:
    """Filter by category ("disk") or exact id ("cpu.temperature")."""

    def matches(c: Check, names: list[str]) -> bool:
        return any(n == c.category or n == c.id for n in names)

    selected = checks
    if only:
        selected = [c for c in selected if matches(c, only)]
    if skip:
        selected = [c for c in selected if not matches(c, skip)]
    return selected
