"""Whole-machine readings: CPU, memory, swap and disk, read from the kernel. No process is started."""

from __future__ import annotations

import os

from . import procs
from .readings import CpuTicks, Disk, Memory

DATA_VOLUME = procs._backend.DATA_VOLUME if procs._backend else "/"


def cpu_ticks() -> CpuTicks:
    """CPU time since boot, all cores together. Two readings give the share over the time between."""
    return procs._backend.cpu_ticks()


def cpu_share(before: CpuTicks, after: CpuTicks) -> dict[str, float]:
    """Percent of all cores spent in user code, in the kernel and idle between two readings."""
    user, system, idle = after.user - before.user, after.system - before.system, after.idle - before.idle
    total = (user + system + idle) or 1
    return {"user": round(100 * user / total, 1), "system": round(100 * system / total, 1),
            "idle": round(100 * idle / total, 1)}


def memory() -> Memory:
    return procs._backend.memory()


def disk(volume: str = DATA_VOLUME) -> Disk:
    """Space really free where the user's files live: the data volume on macOS (Finder's figure
    counts space it could purge), the filesystem the home folder is on under Linux."""
    stat = os.statvfs(volume if os.path.exists(volume) else "/")
    return Disk(free_gb=round(stat.f_bavail * stat.f_frsize / 2**30, 1),
                total_gb=round(stat.f_blocks * stat.f_frsize / 2**30, 1))
