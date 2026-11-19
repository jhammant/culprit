"""The shapes a reading comes back in, whichever kernel it was read from."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Usage:
    """What a process has used since it started."""

    cpu_seconds: float
    footprint_bytes: int   # memory charged to it, compressed and swapped pages included
    written_bytes: int     # bytes it has written to disk


@dataclass(frozen=True)
class CpuTicks:
    user: int
    system: int
    idle: int


@dataclass(frozen=True)
class Memory:
    free_gb: float
    compressed_gb: float      # RAM the compressor occupies
    stored_gb: float          # what that RAM holds, before compression
    swap_used_gb: float
    swap_total_gb: float
    pressure: int             # 1 normal, 2 warn, 4 critical (the kernel's own levels); 0 when it gives none


@dataclass(frozen=True)
class Disk:
    free_gb: float
    total_gb: float
