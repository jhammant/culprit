"""Readings on macOS: libproc and sysctl for processes, Mach and sysctl for the machine.

Every process reader returns None for a process it cannot read (gone, or another user's).
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import struct
import sys

from .readings import CpuTicks, Memory, Usage

NAME = "macOS"
DATA_VOLUME = "/System/Volumes/Data"
KERNEL_PARENT = None  # the kernel's own threads are not in the process table

if sys.platform == "darwin":
    _libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    _libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
else:  # importable anywhere, so the pure parts can be tested on any platform
    _libc = _libproc = None

_PROC_PIDTBSDINFO, _BSDINFO_SIZE = 3, 136
_CTL_KERN, _KERN_PROC, _KERN_PROC_PID, _KERN_PROCARGS2 = 1, 14, 1, 49
_KINFO_PROC_SIZE, _KINFO_PPID_OFFSET = 648, 560
_RUSAGE_INFO_V2, _RUSAGE_SIZE = 2, 160
_HOST_CPU_LOAD_INFO, _HOST_VM_INFO64, _VM_INFO64_WORDS = 3, 4, 38


def pids() -> set[int]:
    """Every process id on the machine."""
    count = _libproc.proc_listallpids(None, 0)
    buffer = (ctypes.c_int * (count + 256))()
    count = _libproc.proc_listallpids(buffer, ctypes.sizeof(buffer))
    return {pid for pid in buffer[:count] if pid > 0}


def parent(pid: int) -> int | None:
    """The parent's pid. libproc answers for our own processes; for another user's (a setuid
    program such as `ps` runs as root) the kernel's process record answers instead."""
    info = ctypes.create_string_buffer(_BSDINFO_SIZE)
    if _libproc.proc_pidinfo(pid, _PROC_PIDTBSDINFO, 0, info, _BSDINFO_SIZE) == _BSDINFO_SIZE:
        return struct.unpack_from("I", info.raw, 16)[0]
    mib = (ctypes.c_int * 4)(_CTL_KERN, _KERN_PROC, _KERN_PROC_PID, pid)
    size = ctypes.c_size_t(_KINFO_PROC_SIZE)
    record = ctypes.create_string_buffer(_KINFO_PROC_SIZE)
    if _libc.sysctl(mib, 4, record, ctypes.byref(size), None, 0) != 0 or size.value < _KINFO_PPID_OFFSET + 4:
        return None
    return struct.unpack_from("i", record.raw, _KINFO_PPID_OFFSET)[0]


def path(pid: int) -> str | None:
    """The program a process is running."""
    buffer = ctypes.create_string_buffer(4096)
    if _libproc.proc_pidpath(pid, buffer, 4096) <= 0:
        return None
    return buffer.value.decode("utf-8", "replace")


def argv(pid: int) -> list[str] | None:
    """A process's command line. Its environment, which follows in the same kernel buffer, is never read."""
    mib = (ctypes.c_int * 3)(_CTL_KERN, _KERN_PROCARGS2, pid)
    size = ctypes.c_size_t(16384)
    buffer = ctypes.create_string_buffer(size.value)
    if _libc.sysctl(mib, 3, buffer, ctypes.byref(size), None, 0) != 0 or size.value < 8:
        return None
    return parse_procargs(buffer.raw[: size.value])


def parse_procargs(raw: bytes) -> list[str] | None:
    """KERN_PROCARGS2: an argument count, the program's path, padding, then that many arguments."""
    if len(raw) < 8:
        return None
    count = struct.unpack_from("i", raw)[0]
    rest = raw[4:]
    end = rest.find(b"\0")
    if end < 0 or count < 0:
        return None
    words = rest[end:].lstrip(b"\0").split(b"\0")
    return [word.decode("utf-8", "replace") for word in words[:count]]


_timebase: tuple[int, int] | None = None


def _ticks_to_seconds(ticks: int) -> float:
    """Mach time units to seconds (the units are not nanoseconds on Apple silicon)."""
    global _timebase
    if _timebase is None:
        info = (ctypes.c_uint32 * 2)()
        _libc.mach_timebase_info(info)
        _timebase = (info[0] or 1, info[1] or 1)
    return ticks * _timebase[0] / _timebase[1] / 1e9


def usage(pid: int) -> Usage | None:
    """CPU time, memory footprint and bytes written. Only for processes this user may read."""
    buffer = ctypes.create_string_buffer(512)
    if _libproc.proc_pid_rusage(pid, _RUSAGE_INFO_V2, buffer) != 0:
        return None
    return parse_rusage(buffer.raw[:_RUSAGE_SIZE], _ticks_to_seconds)


def parse_rusage(raw: bytes, to_seconds=lambda ticks: ticks / 1e9) -> Usage:
    """rusage_info_v2: a 16-byte uuid, then 18 counters of 8 bytes each."""
    counters = struct.unpack_from("18Q", raw, 16)
    user, system, footprint, written = counters[0], counters[1], counters[7], counters[17]
    return Usage(cpu_seconds=to_seconds(user + system), footprint_bytes=footprint, written_bytes=written)


def _host() -> int:
    _libc.mach_host_self.restype = ctypes.c_uint
    return _libc.mach_host_self()


def cpu_ticks() -> CpuTicks:
    """CPU time since boot, all cores together. Two readings give the share over the time between."""
    words = (ctypes.c_uint * 4)()
    count = ctypes.c_uint(4)
    _libc.host_statistics(_host(), _HOST_CPU_LOAD_INFO, words, ctypes.byref(count))
    user, system, idle, nice = words
    return CpuTicks(user=user + nice, system=system, idle=idle)


def parse_vm_info(raw: bytes, page_size: int) -> dict[str, float]:
    """vm_statistics64 in GB: the page counts culprit reports, picked out of the kernel's struct."""
    free, _active, _inactive, _wired = struct.unpack_from("4I", raw, 0)
    compressor_pages = struct.unpack_from("I", raw, 128)[0]
    stored_pages = struct.unpack_from("Q", raw, 144)[0]
    gb = page_size / 2**30
    return {"free_gb": round(free * gb, 1), "compressed_gb": round(compressor_pages * gb, 1),
            "stored_gb": round(stored_pages * gb, 1)}


def _sysctl(name: str, size: int) -> bytes | None:
    buffer = ctypes.create_string_buffer(size)
    length = ctypes.c_size_t(size)
    if _libc.sysctlbyname(name.encode(), buffer, ctypes.byref(length), None, 0) != 0:
        return None
    return buffer.raw[: length.value]


def memory() -> Memory:
    words = (ctypes.c_uint * _VM_INFO64_WORDS)()
    count = ctypes.c_uint(_VM_INFO64_WORDS)
    _libc.host_statistics64(_host(), _HOST_VM_INFO64, words, ctypes.byref(count))
    vm = parse_vm_info(bytes(words), os.sysconf("SC_PAGE_SIZE"))
    swap = _sysctl("vm.swapusage", 32)
    total, _available, used = struct.unpack_from("3Q", swap) if swap and len(swap) >= 24 else (0, 0, 0)
    level = _sysctl("kern.memorystatus_vm_pressure_level", 4)
    return Memory(swap_used_gb=round(used / 2**30, 1), swap_total_gb=round(total / 2**30, 1),
                  pressure=struct.unpack("i", level)[0] if level and len(level) == 4 else 0, **vm)
