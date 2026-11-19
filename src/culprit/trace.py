"""Who is starting processes.

A process that lives for 20 ms never shows in `top` or Activity Monitor, yet a few dozen of them a
second can take whole cores, most of it in the kernel. The tracer lists the process table every few
milliseconds, notes each new pid with its parent, and afterwards walks every new process up to the
nearest ancestor that was already running when the trace began: the long-lived thing responsible.
"""

from __future__ import annotations

import collections
import os
import time
from dataclasses import dataclass, field

from . import label, procs

UNREAD = "(gone before it could be read)"
ORPHAN = "(parent exited before it could be read)"
KERNEL = "(the kernel)"
SERVICE_MANAGERS = {"launchd", "systemd", "init"}  # what pid 1 is when it starts scheduled jobs and adopts orphans


@dataclass
class Proc:
    pid: int
    ppid: int | None
    path: str | None
    argv: list[str] | None


@dataclass
class Spawner:
    """A process that was already running, and what it (and its short-lived children) started."""

    who: str
    pid: int | None
    count: int
    programs: dict[str, int] = field(default_factory=dict)


@dataclass
class Trace:
    seconds: float
    started: int
    by_program: dict[str, int]
    spawners: list[Spawner]
    commands: dict[str, int]

    @property
    def per_second(self) -> float:
        return self.started / self.seconds if self.seconds else 0.0


def read(pid: int) -> Proc:
    return Proc(pid=pid, ppid=procs.parent(pid), path=procs.path(pid), argv=procs.argv(pid))


def observe(seconds: float, list_pids=procs.pids, read_proc=read, clock=time.monotonic, sleep=time.sleep,
            pause: float = 0.004) -> tuple[dict[int, Proc], dict[int, Proc], float]:
    """Watch the process table for `seconds`.

    Returns the processes running at the start, every process seen (those included) and the time
    really watched. A new process is read at once, then again on each pass while it lives: a fork
    shows its parent's program until it execs its own.
    """
    known = {pid: read_proc(pid) for pid in list_pids()}
    base = dict(known)
    alive: set[int] = set()
    start = clock()
    while clock() - start < seconds:
        current = list_pids()
        for pid in current - known.keys():
            known[pid] = read_proc(pid)
            alive.add(pid)
        for pid in list(alive):
            if pid not in current:
                alive.discard(pid)
                continue
            again = read_proc(pid)
            if again.path:
                known[pid].path = again.path
            if again.argv:
                known[pid].argv = again.argv
        sleep(pause)
    return base, known, clock() - start


def attribute(base: dict[int, Proc], known: dict[int, Proc], seconds: float, home: str | None = None,
              own_pid: int | None = None, kernel_parent: int | None = procs.KERNEL_PARENT) -> Trace:
    """Charge every process started during the watch to the long-lived ancestor that caused it."""
    own_pid = os.getpid() if own_pid is None else own_pid
    # Linux lists the kernel's own threads as processes: children of `kernel_parent` with no command line.
    kernel = lambda proc: kernel_parent is not None and proc.ppid == kernel_parent and not proc.argv
    new = [proc for pid, proc in known.items() if pid not in base and not kernel(proc)]

    def manager(pid: int, proc: Proc) -> str | None:
        """What a service manager is called, or None when `proc` is not one. It is pid 1, unless
        pid 1 is an ordinary program, as it is in a container. BusyBox's init runs as `busybox`
        and is known by its first argument. On Linux pid 1 also starts each user a systemd of
        their own, known by its program: any process may call itself `systemd --user`."""
        if pid == 1:
            names = ([label.program(proc.path)] if proc.path else []) + [os.path.basename(word) for word in (proc.argv or [])[:1]]
            return next((name for name in names if name in SERVICE_MANAGERS), None if proc.path else "pid 1")
        users_own = proc.ppid == 1 and label.program(proc.path) == "systemd" and "--user" in (proc.argv or [])
        return "systemd" if users_own else None

    managers = {pid: name for pid, name in ((pid, manager(pid, proc)) for pid, proc in base.items()) if name}

    def ancestor(proc: Proc) -> int | None:
        seen: set[int] = set()
        pid = proc.ppid
        while pid is not None and pid not in base:
            if pid in seen or pid not in known:
                return None
            seen.add(pid)
            pid = known[pid].ppid
        return pid

    def job(proc: Proc) -> Proc:
        """The new process directly under the service manager that `proc` descends from."""
        seen: set[int] = set()
        while proc.ppid not in base and proc.ppid in known and proc.ppid not in seen:
            seen.add(proc.pid)
            proc = known[proc.ppid]
        return proc

    def described(proc: Proc) -> str:
        """A long-lived process by name, with the app that started it when it is a helper of one."""
        who = label.describe(proc.path, proc.argv, home)
        owner = base.get(proc.ppid) if proc.ppid is not None else None
        app = label.app_of(owner.path) if owner else None
        return f"{who} (started by {app})" if app and not label.app_of(proc.path) else who

    name = lambda proc: label.program(proc.path) if proc.path else UNREAD
    by_root: dict[int | None, list[Proc]] = collections.defaultdict(list)
    for proc in new:
        by_root[ancestor(proc)].append(proc)
    by_root.pop(own_pid, None)  # nothing culprit starts itself is ever anyone's fault
    # A service manager starts every scheduled job, so "launchd" or "systemd" names nobody: what it
    # started is grouped by the job instead, and a job that runs again and again adds up under one name.
    groups: dict[tuple[str, int | None], list[Proc]] = collections.defaultdict(list)
    for root, children in by_root.items():
        for proc in children:
            if root in managers:
                top = job(proc)
                who = (label.describe(top.path, top.argv, home) if top.path else UNREAD) + f" (a job {managers[root]} starts)"
                groups[who, None].append(proc)
            else:
                started_by_kernel = root is not None and (root == kernel_parent or kernel(base[root]))
                who = ORPHAN if root is None else KERNEL if started_by_kernel else described(base[root])
                groups[who, root].append(proc)
    spawners = [Spawner(who=who, pid=pid, count=len(children),
                        programs=dict(collections.Counter(name(c) for c in children).most_common(4)))
                for (who, pid), children in groups.items()]
    spawners.sort(key=lambda s: -s.count)
    counted = [proc for children in by_root.values() for proc in children]
    commands = collections.Counter(
        label.describe(p.path, p.argv, home, width=120) if p.path else UNREAD for p in counted)
    return Trace(seconds=seconds, started=len(counted),
                 by_program=dict(collections.Counter(name(p) for p in counted).most_common(12)),
                 spawners=spawners, commands=dict(commands.most_common(15)))


def trace(seconds: float = 30.0) -> Trace:
    """Watch for `seconds` and say who started what."""
    procs.require_supported()
    base, known, elapsed = observe(seconds)
    return attribute(base, known, elapsed)
