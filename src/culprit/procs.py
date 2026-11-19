"""Facts about processes, read straight from the kernel: libproc and sysctl on macOS, /proc on Linux.

Nothing here starts a process. A tool that looks for what is loading a machine should not add to
the load, and `ps` in a loop is one of the commonest culprits there is.

Every reader returns None for a process it cannot read.
"""

from __future__ import annotations

import sys

from .readings import Usage

if sys.platform == "darwin":
    from . import _macos as _backend
elif sys.platform.startswith("linux"):
    from . import _linux as _backend
else:  # importable anywhere, so the pure parts can be tested on any platform
    _backend = None

OS_NAME = _backend.NAME if _backend else None
KERNEL_PARENT = _backend.KERNEL_PARENT if _backend else None  # the parent of the kernel's own threads, if they show


class Unsupported(RuntimeError):
    """This machine is not one culprit can read (it needs macOS or Linux)."""


def require_supported() -> None:
    if _backend is None:
        raise Unsupported("culprit reads libproc on macOS and /proc on Linux, and this machine has neither")


def pids() -> set[int]:
    """Every process id on the machine."""
    return _backend.pids()


def parent(pid: int) -> int | None:
    """The parent's pid."""
    return _backend.parent(pid)


def path(pid: int) -> str | None:
    """The program a process is running."""
    return _backend.path(pid)


def argv(pid: int) -> list[str] | None:
    """A process's command line. Its environment is never read."""
    return _backend.argv(pid)


def usage(pid: int) -> Usage | None:
    """CPU time, memory footprint and bytes written."""
    return _backend.usage(pid)
