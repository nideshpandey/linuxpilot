"""Shared test helpers.

Tests never depend on the developer's machine: each one builds a fake root
directory containing only the /proc, /sys and /etc files it needs, and
replaces command execution and statvfs with fakes.
"""

from __future__ import annotations

import os
from collections import namedtuple

import pytest

from linuxpilot.context import CommandResult, Context

FakeStatvfs = namedtuple(
    "FakeStatvfs",
    "f_bsize f_frsize f_blocks f_bfree f_bavail f_files f_ffree f_favail f_flag f_namemax",
)


def statvfs(total_gb=100, used_pct=50, inodes=1_000_000, inode_used_pct=10, block=4096):
    blocks = int(total_gb * 1024**3 / block)
    used = int(blocks * used_pct / 100)
    free = blocks - used
    files = inodes
    ffree = int(inodes * (100 - inode_used_pct) / 100) if inodes else 0
    return FakeStatvfs(block, block, blocks, free, free, files, ffree, ffree, 0, 255)


@pytest.fixture
def make_ctx(tmp_path):
    """make_ctx(files={...}, commands={...}, statvfs={...}, cores=4) -> Context."""

    def factory(files=None, commands=None, statvfs_map=None, cores=4, **kwargs):
        for path, content in (files or {}).items():
            full = tmp_path / path.lstrip("/")
            full.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                full.write_bytes(content)
            else:
                full.write_text(content)

        def run_impl(args, timeout):
            key = " ".join(args)
            for pattern, result in (commands or {}).items():
                if key.startswith(pattern):
                    if isinstance(result, Exception):
                        raise result
                    if isinstance(result, CommandResult):
                        return result
                    return CommandResult(0, result, "")
            from linuxpilot.context import CommandNotFound

            raise CommandNotFound(args[0])

        def statvfs_impl(path):
            value = (statvfs_map or {}).get(path)
            if value is None:
                raise FileNotFoundError(path)
            if isinstance(value, Exception):
                raise value
            return value

        return Context(
            root=str(tmp_path),
            run_impl=run_impl,
            statvfs_impl=statvfs_impl,
            cpu_count_impl=lambda: cores,
            sample_seconds=0.01,
            **kwargs,
        )

    return factory


def mkdir(ctx: Context, path: str) -> None:
    os.makedirs(ctx.path(path), exist_ok=True)
