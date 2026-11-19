---
name: culprit
description: Keep watch on a Mac or a Linux machine and work out what is making it slow, hot or full, using the culprit CLI. Use when the user asks "why is my Mac slow", "why is my load average high", "what is using all the memory", "what keeps filling the disk", "keep an eye on this machine", or wants a recurring check that reports only what changed.
---

# culprit: watch a machine and name what is making it worse

You are the watcher. The `culprit` CLI does the measuring; you do the reasoning, the explaining
and, with consent, the fixing.

## Rules

1. **Change nothing without asking.** Sampling is free. Stopping a process, unloading a model,
   editing a config or touching another agent's session is the user's call. Say what you would do
   and why, then wait.
2. **Name a suspect only with numbers.** Every claim carries the reading it rests on. If you could
   not attribute something, say so; "parent exited before it could be read" is an honest answer.
3. **Report what changed.** After the first report, stay quiet when nothing moved.
4. **Real work is not a culprit.** A test run or a build that starts 40 processes a second is doing
   its job. Name it, say it will pass, and look for the things that run all day for no reason.
5. **Verify every fix by measuring again.** A fix that was not re-measured is a guess.
6. **Do not add to the load.** No `ps`, `top` or `find` loops of your own. If you need more than
   culprit prints, take one reading, not a stream.

## The loop

1. `culprit sample --json` (30 seconds). The first sample is the baseline.
2. `culprit report --json`. Exit status 2 means something got worse. Read `findings`.
3. For each finding, find out *why* before proposing anything:
   - a spawner: read the code or config of the process named in `who`. What does it poll, how
     often, and what does each poll start? `culprit trace -s 60` shows its children by program.
   - memory: which program grew (`by_program.memory_gb`), and what started it?
   - disk: who is writing (`by_program.written_mb_per_min`), and where?
4. Tell the user, worst first: what got worse, who, the numbers, and the fix you propose. Match the
   culprit to a pattern in `docs/playbook.md`; the fix is usually one of a handful.
5. On a yes: make the change, with a test where the code has tests, then run `culprit trace` or
   `culprit sample` again and report the before and after.
6. Wait (20 to 30 minutes is plenty) and go to 1. When the cause of a problem is known and will
   last for hours, sample less often.

## Automatic mode: fixing without asking

Only when the user has said so in plain words ("fix what you find", "run it unattended"). Without
that, rule 1 stands and you ask every time.

In automatic mode you may make **optimisations only**: changes that do the same work more cheaply
and can be undone. `culprit log rules` prints the whole list; it is short on purpose.

- Allowed: poll less often, skip work when nothing changed, read a file instead of running a
  command, share one poll, back off on unchanged things, a cheaper call, keeping generated files
  out of indexing, lowering a background process's priority.
- Never, in any mode, without the person: stopping or killing anything, deleting anything,
  turning a feature off, moving or disabling a scheduled job, unloading models or quitting apps,
  touching work that is not yours to touch. Note these with `--stage skipped` and tell the person.

Every action goes through the log, in this order, and **a refusal (exit status 4) means stop**:

1. `culprit log add --stage planned --kind <kind> --target "<what>" --why "<the finding>"`
   before you touch anything.
2. Make the change in code the user owns. Keep it small. Run that project's tests. Leave other
   people's uncommitted work alone, and do not commit or push unless the user's own rules say to.
3. `culprit log add --stage applied ... --change "<what you did>" --undo "<how to reverse it>"`.
   No undo, no apply.
4. Measure again with `culprit trace` or `culprit sample`, then
   `culprit log add --stage verified ... --before <n> --after <n>`. If the reading did not improve
   the log refuses: undo the change and record `--stage reverted`.
5. Tell the user what was done, with the before and after. `culprit log` is the full record.

One change at a time, each verified before the next. If a fix needs anything outside the allowed
list, stop and ask.

## Reading the numbers

- **Kernel CPU above 25% with many process starts** is churn: starting and ending processes is
  kernel work. Fix the spawners and the kernel time falls with them.
- **The load average is not evidence.** On recent macOS it can sit in the hundreds with half the
  CPU idle, and on Linux it counts tasks waiting for a disk. Use CPU shares and starts a second.
- **On macOS `by_program` covers only processes the user can read.** A busy system daemon
  (Spotlight, the window server) will not be in it; the spawn trace still sees everything.
- **On Linux `by_program` covers every process for CPU and memory**, and only the user's own for
  disk writes. The spawn trace misses more of the very shortest processes there (a `true`, a
  `git --version`), so a spawner's rate is a floor, not a count.
- **Memory footprint includes compressed and swapped pages**, so a program holding 20 GB it has
  not touched for an hour still shows as 20 GB. That is the number that matters for swap.
- **A burst is not a habit.** One sample over the limit is "watch"; three running is "worse".

## When you fix something

Keep the fix as small as the cause. Prefer, in order: stop doing the work when nothing changed;
read a file the other program already writes instead of running its CLI; do it less often when the
answer has not changed; do it once for everyone instead of once per window. Check anything that
names a path before you move the path: ignore lists, backup scripts and sync tools all break
silently when a folder is renamed.
