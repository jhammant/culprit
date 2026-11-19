"""Readings on Linux, all of them files under /proc and /sys.

Linux tells an ordinary user more than macOS does: any process's parent, command line, CPU time
and memory. What it keeps for the owner is the link to the program a process runs and the bytes
it has written. So another user's process is named by the short name the kernel keeps for it, and
is charged no disk writes.

Every process reader returns None for a process it cannot read (gone, or a kernel thread).
"""

from __future__ import annotations

import os

from . import label
from .readings import CpuTicks, Memory, Usage

NAME = "Linux"
DATA_VOLUME = os.path.expanduser("~")  # where a user's files grow; often its own filesystem
PRESSURE_WARN, PRESSURE_CRITICAL = 10.0, 25.0  # percent of the last ten seconds spent waiting for memory


def _read(name: str) -> bytes | None:
    """A file's contents, or None when it has gone or is not ours to read."""
    try:
        with open(name, "rb") as handle:
            return handle.read()
    except OSError:
        return None


def _numbers(raw: bytes) -> dict[str, int]:
    """Lines of `Name:   123 kB` as a dict. Lines that hold no number are left out."""
    out = {}
    for line in raw.decode("ascii", "replace").splitlines():
        name, _, value = line.partition(":")
        first = value.split()[:1]
        if first and first[0].isdigit():
            out[name] = int(first[0])
    return out


# The kernel's own threads are in the process table, all children of kthreadd, which is pid 2
# unless this is a container. They are not programs anyone started.
KERNEL_PARENT = 2 if (_read("/proc/2/comm") or b"").strip() == b"kthreadd" else None


def pids() -> set[int]:
    """Every process id on the machine."""
    return {int(name) for name in os.listdir("/proc") if name.isdigit()}


def parse_stat(raw: bytes) -> tuple[str, int, int] | None:
    """/proc/<pid>/stat: the pid, a name in brackets, then numbers. The name is whatever the
    program set, so only its last bracket ends it. Returns the name, the parent's pid and the CPU
    time used, in clock ticks."""
    start, end = raw.find(b"("), raw.rfind(b")")
    fields = raw[end + 1:].split()
    if start < 0 or end < start or len(fields) < 13:
        return None
    try:
        return raw[start + 1:end].decode("utf-8", "replace"), int(fields[1]), int(fields[11]) + int(fields[12])
    except ValueError:
        return None


def parent(pid: int) -> int | None:
    """The parent's pid. Anyone may read it for any process."""
    stat = parse_stat(_read(f"/proc/{pid}/stat") or b"")
    return stat[1] if stat else None


def path(pid: int) -> str | None:
    """The program a process is running. Only its owner may follow the link to it, so another
    user's process is named by the short name the kernel keeps for it."""
    try:
        return os.readlink(f"/proc/{pid}/exe").removesuffix(" (deleted)")
    except PermissionError:
        pass  # someone else's, or one that has closed itself to readers
    except OSError:
        return None  # gone, or on its way out
    words = argv(pid)
    stat = parse_stat(_read(f"/proc/{pid}/stat") or b"") if words else None
    return parse_name(words, stat[0] if stat else None)


def parse_name(words: list[str] | None, short: str | None) -> str | None:
    """What to call a process whose program link is closed to us: the short name the kernel keeps
    for it, which is at most 15 characters. The process can set that name, so it is redacted.
    The first word of its command line is not used: that is whatever the process says it is, and
    a program's name is printed and kept as it stands."""
    if not words or not short:
        return None  # no command line: a kernel thread, or a process that has exited
    return label.redact([short])[0]


def parse_cmdline(raw: bytes) -> list[str] | None:
    """/proc/<pid>/cmdline: the arguments, each ended by a NUL. A program that rewrites its title
    leaves one run of text with spaces where the NULs were. That is split on the spaces, or a
    secret in it would sit inside one long word, where redaction does not look."""
    raw = raw.rstrip(b"\0")
    if not raw:
        return None
    words = raw.split(b"\0")
    if len(words) == 1:
        words = raw.split() or words
    return [word.decode("utf-8", "replace") for word in words]


def argv(pid: int) -> list[str] | None:
    """A process's command line. Its environment is in another file, which is never opened."""
    return parse_cmdline(_read(f"/proc/{pid}/cmdline") or b"")


def parse_footprint(raw: bytes) -> int | None:
    """/proc/<pid>/status: the memory that is this process's alone, in RAM or swapped out. Mapped
    files and shared memory are left out, because every process sharing them would be charged."""
    kb = _numbers(raw)
    if "VmRSS" not in kb:
        return None
    return (kb.get("RssAnon", kb["VmRSS"]) + kb.get("VmSwap", 0)) * 1024


def parse_written(raw: bytes) -> int:
    """/proc/<pid>/io: bytes sent to storage, less those deleted again before they got there. A
    process that deletes what others wrote reads below zero, so only the change between two
    readings means anything."""
    count = _numbers(raw)
    return count.get("write_bytes", 0) - count.get("cancelled_write_bytes", 0)


_ticks_per_second: int | None = None


def usage(pid: int) -> Usage | None:
    """CPU time, memory and bytes written. The first two are there for any process; bytes written
    are for its owner only, and count as none when they cannot be read."""
    global _ticks_per_second
    if _ticks_per_second is None:
        _ticks_per_second = os.sysconf("SC_CLK_TCK")
    stat = parse_stat(_read(f"/proc/{pid}/stat") or b"")
    footprint = parse_footprint(_read(f"/proc/{pid}/status") or b"")
    if stat is None or footprint is None:
        return None
    return Usage(cpu_seconds=stat[2] / _ticks_per_second, footprint_bytes=footprint,
                 written_bytes=parse_written(_read(f"/proc/{pid}/io") or b""))


def parse_cpu(raw: bytes) -> CpuTicks:
    """The first line of /proc/stat: user, nice, system, idle, iowait, irq and softirq time for all
    cores together. Handling interrupts is kernel work; waiting for a disk is idle time."""
    try:
        ticks = [int(word) for word in raw.split(b"\n", 1)[0].split()[1:8]]
    except ValueError:
        ticks = []  # not the line it should be: no reading, rather than a wrong one
    user, nice, system, idle, iowait, irq, softirq = ticks + [0] * (7 - len(ticks))
    return CpuTicks(user=user + nice, system=system + irq + softirq, idle=idle + iowait)


def cpu_ticks() -> CpuTicks:
    """CPU time since boot, all cores together. Two readings give the share over the time between."""
    return parse_cpu(_read("/proc/stat") or b"")


def parse_pressure(raw: bytes | None) -> int:
    """/proc/pressure/memory, on the scale macOS uses: 1 normal, 2 warn, 4 critical, and 0 when the
    kernel keeps no figures. `some` is the share of the last ten seconds in which at least one
    task waited for memory; `full` is the share in which every task did."""
    share = {}
    for line in (raw or b"").decode("ascii", "replace").splitlines():
        kind, _, rest = line.partition(" ")
        for pair in rest.split():
            if pair.startswith("avg10="):
                try:
                    share[kind] = float(pair[6:])
                except ValueError:
                    pass
    if "some" not in share:
        return 0
    if share.get("full", 0.0) >= PRESSURE_CRITICAL:
        return 4
    return 2 if share["some"] >= PRESSURE_WARN else 1


def parse_zram(raw: bytes) -> tuple[int, int] | None:
    """/sys/block/zram*/mm_stat: the bytes a compressed RAM disk holds and the RAM it uses for them."""
    fields = raw.split()
    try:
        return int(fields[0]), int(fields[2])
    except (IndexError, ValueError):
        return None


def parse_memory(meminfo: bytes, pressure: int, zram: list[tuple[int, int]]) -> Memory:
    """/proc/meminfo in GB. Free is what could be handed out, not what is empty: Linux keeps
    spare memory full of file cache. Compressed memory is zswap's pool plus any zram devices."""
    kb = _numbers(meminfo)
    stored = kb.get("Zswapped", 0) * 1024 + sum(held for held, _used in zram)
    compressed = kb.get("Zswap", 0) * 1024 + sum(used for _held, used in zram)
    total, free = kb.get("SwapTotal", 0), kb.get("SwapFree", 0)
    return Memory(free_gb=round(kb.get("MemAvailable", kb.get("MemFree", 0)) / 2**20, 1),
                  compressed_gb=round(compressed / 2**30, 1), stored_gb=round(stored / 2**30, 1),
                  swap_used_gb=round((total - free) / 2**20, 1), swap_total_gb=round(total / 2**20, 1),
                  pressure=pressure)


def memory() -> Memory:
    try:
        devices = [name for name in os.listdir("/sys/block") if name.startswith("zram")]
    except OSError:
        devices = []
    zram = [held for held in (parse_zram(_read(f"/sys/block/{name}/mm_stat") or b"") for name in devices) if held]
    return parse_memory(_read("/proc/meminfo") or b"", parse_pressure(_read("/proc/pressure/memory")), zram)
