"""One sample of the machine, and the history file samples are kept in.

A sample is a plain dict so it can be written as a line of JSON, read back by a later version and
handed to an agent as it is.
"""

from __future__ import annotations

import collections
import json
import os
import time
from pathlib import Path

from . import label, procs, system, trace as tracing

KEEP_SAMPLES = 2000


def family(path: str | None, parent_path: str | None = None) -> str:
    """The app or program a process belongs to, so an app's helpers count as the app.

    A program that is not in an app but was started by one is named with it (`node (Models.app)`):
    a helper holding 20 GB should not read as an anonymous `node`.
    """
    if not path:
        return "(unreadable)"
    if "/CoreSimulator/" in path:
        return "iOS Simulator"
    own = label.app_of(path)
    if own:
        return own
    started_by = label.app_of(parent_path)
    return f"{label.program(path)} ({started_by})" if started_by else label.program(path)


def _usage(pids) -> dict[int, tuple[str, procs.Usage]]:
    out = {}
    for pid in pids:
        used = procs.usage(pid)
        if used is not None:
            parent = procs.parent(pid)
            out[pid] = (family(procs.path(pid), procs.path(parent) if parent else None), used)
    return out


def by_family(before: dict, after: dict, seconds: float, cores: int) -> dict[str, dict[str, float]]:
    """CPU (cores in use), memory (GB) and disk writes (MB a minute) by program, the largest of each.

    On macOS only processes this user may read are in it: run culprit with sudo to see system
    daemons too. On Linux every process is, though disk writes are counted for the user's own only.
    """
    cpu, written, memory = collections.Counter(), collections.Counter(), collections.Counter()
    for pid, (name, used) in after.items():
        memory[name] += used.footprint_bytes
        earlier = before.get(pid)
        if earlier is None:
            continue
        cpu[name] += max(0.0, used.cpu_seconds - earlier[1].cpu_seconds)
        written[name] += max(0, used.written_bytes - earlier[1].written_bytes)
    top = lambda counter, scale, digits, floor: {
        name: round(value * scale, digits) for name, value in counter.most_common(8) if value * scale >= floor}
    return {"cpu_cores": top(cpu, 1 / seconds, 2, 0.05), "memory_gb": top(memory, 1 / 2**30, 1, 0.5),
            "written_mb_per_min": top(written, 60 / seconds / 2**20, 1, 1.0)}


def take(seconds: float = 30.0) -> dict:
    """Watch the machine for `seconds` and describe it."""
    procs.require_supported()
    ticks = system.cpu_ticks()
    before = _usage(procs.pids())
    base, known, elapsed = tracing.observe(seconds)
    traced = tracing.attribute(base, known, elapsed)
    after = _usage(procs.pids())
    memory, disk = system.memory(), system.disk()
    return {
        "at": round(time.time()), "window_s": round(elapsed, 1), "os": procs.OS_NAME,
        "cpu": system.cpu_share(ticks, system.cpu_ticks()),
        "memory": vars(memory), "disk": vars(disk), "processes": len(after) or len(known),
        "spawns": {
            "per_second": round(traced.per_second, 1),
            "by_program": {name: round(n / elapsed, 2) for name, n in traced.by_program.items()},
            "spawners": [{"who": s.who, "pid": s.pid, "per_second": round(s.count / elapsed, 2), "programs": s.programs}
                         for s in traced.spawners[:10]],
            "commands": {line: round(n / elapsed, 2) for line, n in list(traced.commands.items())[:10]},
        },
        "by_program": by_family(before, after, elapsed, os.cpu_count() or 1),
    }


def history_path() -> Path:
    state = os.environ.get("CULPRIT_STATE_DIR") or os.path.join(
        os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), "culprit")
    return Path(state) / "history.jsonl"


def load(path: Path | None = None) -> list[dict]:
    path = path or history_path()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue  # a line cut short by a crash loses only itself
        if isinstance(row, dict) and "at" in row:
            out.append(row)
    return out


def save(sample: dict, path: Path | None = None) -> Path:
    """Append a sample. The file is this user's only (command lines are redacted, but still private)."""
    path = path or history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = load(path)[-(KEEP_SAMPLES - 1):] + [sample]
    temp = path.with_suffix(".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.writelines(json.dumps(row, separators=(",", ":")) + "\n" for row in rows)
    os.replace(temp, path)
    return path
