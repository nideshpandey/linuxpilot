"""Access to the system, isolated behind one object so checks are testable.

Checks never call open(), os.statvfs() or subprocess directly. They go through
a Context. Tests create a Context whose ``root`` points at a fixture directory
containing fake /proc and /sys files, and replace ``run``/``statvfs`` as needed.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable


class CommandNotFound(Exception):
    """The external command is not installed."""


class CommandTimeout(Exception):
    """The external command did not finish in time."""


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


DEFAULT_THRESHOLDS: dict[str, float] = {
    # Disk space and inodes, percent used.
    "disk_warning_percent": 85,
    "disk_critical_percent": 95,
    "inode_warning_percent": 85,
    "inode_critical_percent": 95,
    # Memory available, percent of total (lower is worse).
    "memory_available_warning_percent": 10,
    "memory_available_critical_percent": 5,
    # Memory pressure (PSI "some avg60"), percent of time stalled.
    "memory_pressure_warning_percent": 20,
    # Load average (5 min) per CPU core.
    "load_per_core_warning": 1.5,
    "load_per_core_critical": 3.0,
    # CPU sample, percent of time.
    "iowait_warning_percent": 20,
    "steal_warning_percent": 10,
    # Temperature, degrees Celsius.
    "temperature_warning_c": 80,
    "temperature_critical_c": 90,
}


@dataclass
class Context:
    """Everything a check needs to look at the system."""

    root: str = "/"
    network: bool = True
    quick: bool = False
    sample_seconds: float = 0.5
    network_timeout: float = 3.0
    dns_test_name: str = "example.com"
    connectivity_targets: tuple = (("1.1.1.1", 443), ("9.9.9.9", 443))
    thresholds: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_THRESHOLDS))
    # Replaceable in tests:
    run_impl: Callable[..., CommandResult] | None = None
    statvfs_impl: Callable[[str], Any] | None = None
    cpu_count_impl: Callable[[], int] | None = None

    # ----- files -------------------------------------------------------
    def path(self, p: str) -> str:
        """Map an absolute system path into the (possibly fake) root."""
        if self.root in ("", "/"):
            return p
        return os.path.join(self.root, p.lstrip("/"))

    def read(self, p: str) -> str:
        """Read a text file. Raises FileNotFoundError / PermissionError."""
        with open(self.path(p), "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()

    def read_or_none(self, p: str) -> str | None:
        try:
            return self.read(p)
        except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
            return None

    def exists(self, p: str) -> bool:
        return os.path.exists(self.path(p))

    def glob(self, pattern: str) -> list[str]:
        """Glob inside the root; returns *system* paths (root prefix removed)."""
        matches = sorted(glob.glob(self.path(pattern)))
        if self.root in ("", "/"):
            return matches
        prefix = os.path.normpath(self.root)
        return ["/" + os.path.relpath(m, prefix) for m in matches]

    def statvfs(self, p: str) -> Any:
        if self.statvfs_impl is not None:
            return self.statvfs_impl(p)
        return os.statvfs(self.path(p))

    def cpu_count(self) -> int:
        if self.cpu_count_impl is not None:
            return self.cpu_count_impl()
        try:
            return len(os.sched_getaffinity(0)) or 1
        except (AttributeError, OSError):
            return os.cpu_count() or 1

    # ----- commands ----------------------------------------------------
    def which(self, name: str) -> str | None:
        return shutil.which(name)

    def run(self, args: list[str], timeout: float = 5.0) -> CommandResult:
        """Run a command with an explicit argument list (never a shell).

        Raises CommandNotFound or CommandTimeout. A non-zero exit code is
        returned, not raised, because many tools use it to report state.
        """
        if self.run_impl is not None:
            return self.run_impl(args, timeout)
        if not args or self.which(args[0]) is None:
            raise CommandNotFound(args[0] if args else "")
        env = dict(os.environ, LC_ALL="C", LANG="C")
        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CommandTimeout(" ".join(args)) from exc
        except FileNotFoundError as exc:
            raise CommandNotFound(args[0]) from exc
        return CommandResult(proc.returncode, proc.stdout, proc.stderr)
