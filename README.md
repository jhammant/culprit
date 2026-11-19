# culprit

Find what is making your Mac or Linux machine worse: who is starting processes, eating memory and
filling the disk.

`top` and Activity Monitor show you what is busy *right now*. They cannot show a process that lives
for 20 milliseconds, and a few dozen of those a second will take whole cores, most of it kernel
time, while every list of "top processes" looks calm. culprit catches them and names the long-lived
program that keeps starting them.

- No root, no dependencies, one small Python package.
- It starts no processes itself. A monitor that runs `ps` in a loop is part of the problem.
- Command lines are redacted before they are printed or kept.
- macOS and Linux. It reads the kernel directly: `libproc` on one, `/proc` on the other.

## Install

```bash
uv tool install git+https://github.com/jhammant/culprit
# or
pipx install git+https://github.com/jhammant/culprit
```

## Use

```bash
culprit trace            # who is starting processes, over 30 seconds
culprit sample           # one full sample, added to the history
culprit report           # what got worse since the earlier samples, and who is behind it
culprit watch --every 20m
```

The output below is from a real machine with the names changed. `culprit trace` charges every
process that started during the trace to the nearest ancestor that was already running:

```text
1,247 processes started in 30 s: 41.6 a second

who started them (the nearest ancestor that was already running)
   12.40/s  python3 dashboard.py --serve 8080 [1796]
            211 git, 98 ps, 41 security
    8.10/s  Sidebar.app [1818]
            160 ps, 59 security, 18 agent
    6.25/s  agent --resume 4f1c9a2e [25899]
            120 ps, 30 uv, 30 Python
    0.40/s  python3 waker.py [1717]
            12 docker
```

`culprit sample` adds CPU, memory, swap, disk and the biggest users of each, by app:

```text
20:14  CPU 26% user, 23% kernel, 52% idle  |  swap 9 of 10 GB, 28.8 GB compressed, pressure 1  |  disk 113 GB free  |  54 processes started a second
  CPU (cores): Containers.app 1.67, Terminal.app 0.85, Editor.app 0.8
  memory (GB): node (Models.app) 44.1, Browser.app 14.6, Containers.app 13.3
  disk writes (MB/min): Builder.app 6.9, Containers.app 6.6
```

`culprit report` compares the newest sample with the earlier ones. Every finding names a suspect
and the numbers it rests on:

```text
[worse] Swap grew 2.4 GB to 9 GB since the last sample. Suspect: node (Models.app) +22 GB. (memory pressure level 1, 2.3 GB free)
[worse] Free disk fell 89 GB in 4.4 h to 113 GB. Suspect: Builder.app 6.9 MB/min. (at this rate it is full in 6 h)
[watch] One process is starting 6 processes a second. Suspect: python3 dashboard.py --serve 8080. (mostly 90 git)
[better] A process that was starting 39 processes a second has stopped. Suspect: pytest -q. (now 0 a second)
```

`report` exits 2 when something got worse, so a script or an agent can act on it. Every command
takes `--json`.

## The loop: let an agent keep watch

culprit was built during one long session of asking an AI coding agent "why is my load average so
high again?". The useful part turned out to be a loop: sample, name the culprit, fix it with
consent, sample again to prove the fix. `skills/culprit/SKILL.md` is that loop written down as a
Claude Code skill. Copy the folder into `~/.claude/skills/` and ask the agent to "keep an eye on
this Mac". It reports only what changed, and it never changes anything without asking.

[docs/playbook.md](docs/playbook.md) lists the kinds of culprit it found and the fix for each. In
that session process starts went from 29 to 43 a second down to about 10 with the machine otherwise
quiet, and none of the fixes removed a feature.

## Automatic fixes, and the log

culprit itself never changes anything on your machine. The agent following the skill can, and you
can let it do so unattended. Two things hold it in:

- **Only optimisations.** `culprit log rules` lists the eight kinds of change an unattended run
  may make. Each one does the same work more cheaply: poll less often, skip work when nothing
  changed, read a file instead of running a command, back off on unchanged things. Stopping a
  process, deleting anything, turning a feature off, moving a scheduled job and unloading models
  are refused outright and left for you.
- **Everything is logged, in order.** An action is recorded as planned before it is made, as
  applied with how to undo it, then as verified with the reading before and after. A change that
  did not improve the reading cannot be marked verified; it has to be reverted, and that is
  logged too. The log (`~/.local/state/culprit/actions.jsonl`) is append-only.

```text
$ culprit log
18 Nov 21:02  planned  skip-when-unchanged: status plugin poll
      why: 6 ps a second for 3 samples
18 Nov 21:09  applied  skip-when-unchanged: status plugin poll
      why: 6 ps a second for 3 samples
      what: stat the runs folder before starting the poll
      undo: git revert 3f2a1c9
18 Nov 21:12  verified skip-when-unchanged: status plugin poll (6 to 0 a second)
      why: 6 ps a second for 3 samples
```

This is a gate and a record, not a sandbox. It refuses to record an action that breaks the rules,
and the skill treats a refusal as a stop, but it cannot restrain an agent that ignores the skill.

## How it works

- **Spawn tracing.** The process table is listed every few milliseconds: through
  `proc_listallpids` on macOS, by listing `/proc` on Linux. Each new pid is read once for its
  parent and program, then again while it lives, because a fork looks like its parent until it
  execs. After the trace, each new process is walked up its parents to the first one that existed
  when the trace began.
- **Other users' processes.** On macOS `ps` itself is setuid root, so `libproc` will not give its
  parent to an ordinary user. The kernel's process record (`sysctl kern.proc.pid`) does. Linux
  gives any user every process's parent and command line but keeps the link to its program for
  the owner, so another user's process is named by the short name the kernel keeps for it
  (15 characters at most).
- **Per-program numbers.** On macOS `proc_pid_rusage` gives CPU time, memory footprint (compressed
  and swapped pages included) and bytes written, read at the start and end of the window. On
  Linux they come from `/proc/<pid>/stat`, `status` and `io`; memory is what is the process's
  alone, in RAM or swapped out, so shared libraries and file cache are not charged to anyone.
- **Machine numbers.** On macOS `host_statistics64` for memory and the compressor,
  `host_statistics` for CPU, `sysctl` for swap and the memory pressure level, `statvfs` for disk.
  On Linux `/proc/stat` and `/proc/meminfo`, where free memory is what the kernel could hand out
  rather than what is empty, and zswap and zram are the compressor. The pressure level is 2 when
  some task spent a tenth of the last ten seconds waiting for memory and 4 when every task spent
  a quarter of it (`/proc/pressure/memory`).

## What it cannot see

- A process that starts and exits between two looks (a few milliseconds) is missed. One whose
  parent has already exited is listed as "parent exited before it could be read", never guessed.
  Linux starts a process in well under a millisecond, so more slip through there: a `true` or a
  `git --version` is usually missed, a `ps` or a Python script is not.
- On macOS, CPU, memory and disk writes by program cover the processes your user may read. System
  daemons (WindowServer, Spotlight, the kernel) are missing from those three lists unless you run
  culprit with `sudo`. The spawn trace covers every process either way.
- On Linux, CPU and memory by program cover every process, and disk writes cover your own unless
  you run culprit with `sudo`. The kernel's own threads are left out. Where `/proc` is mounted
  with `hidepid`, other users' processes are hidden from all of it.
- The load average. On recent macOS it can read in the hundreds while half the CPU is idle, so
  culprit reports CPU shares and process starts and leaves it out.

## Privacy

Command lines can hold tokens and passwords. Before anything is printed or saved, culprit replaces
the value after a flag named like a secret (`--token`, `--api-key`), the value of a pair named like
one (`API_KEY=…`), passwords inside URLs, and any long run of mixed letters and digits. A
process's environment is never read. The history file
(`~/.local/state/culprit/history.jsonl`) is readable by you alone and capped at 2,000 samples.
Nothing leaves the machine.

## Development

```bash
uv run pytest
```

The tests cover the attribution, the findings and the redaction with made-up process tables, and
check what is read against the running kernel on macOS and on Linux.

## Licence

MIT.
