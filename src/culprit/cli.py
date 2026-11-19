"""culprit: find what is making your Mac or Linux machine worse.

  culprit trace             who is starting processes, over 30 seconds
  culprit sample            one full sample (CPU, memory, disk, spawns), kept in the history
  culprit report            what got worse since the earlier samples, and who is behind it
  culprit watch             sample and report on a timer until stopped
  culprit log               every change made in culprit's name, and how to undo it
  culprit log add ...       record a change (refused unless it is an optimisation)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict

from . import __version__, actions, diagnose, procs, sample, trace as tracing


def _seconds(text: str) -> float:
    """"90", "90s", "20m" or "2h" as seconds."""
    units = {"s": 1, "m": 60, "h": 3600}
    text = text.strip().lower()
    try:
        return float(text[:-1]) * units[text[-1]] if text[-1:] in units else float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a length of time: {text!r} (try 30, 90s, 20m or 2h)")


def show_trace(traced: tracing.Trace) -> None:
    print(f"{traced.started} processes started in {traced.seconds:.0f} s: {traced.per_second:.1f} a second\n")
    print("who started them (the nearest ancestor that was already running)")
    for spawner in traced.spawners[:12]:
        mix = ", ".join(f"{n} {name}" for name, n in spawner.programs.items())
        pid = f" [{spawner.pid}]" if spawner.pid else ""
        print(f"  {spawner.count / traced.seconds:6.2f}/s  {spawner.who}{pid}\n            {mix}")
    print("\nwhat was started")
    for name, count in traced.by_program.items():
        print(f"  {count / traced.seconds:6.2f}/s  {name}")
    print("\nmost repeated command lines")
    for line, count in list(traced.commands.items())[:10]:
        print(f"  {count:5d}  {line}")


def show_sample(row: dict) -> None:
    cpu, memory, disk, spawns = row["cpu"], row["memory"], row["disk"], row["spawns"]
    print(f"{time.strftime('%H:%M', time.localtime(row['at']))}  CPU {cpu['user']:g}% user, {cpu['system']:g}% kernel, {cpu['idle']:g}% idle"
          f"  |  swap {memory['swap_used_gb']:g} of {memory['swap_total_gb']:g} GB, {memory['compressed_gb']:g} GB compressed,"
          f" pressure {memory['pressure']}  |  disk {disk['free_gb']:g} GB free  |  {spawns['per_second']:g} processes started a second")
    programs = row.get("by_program", {})
    for title, key, unit in (("CPU", "cpu_cores", "cores"), ("memory", "memory_gb", "GB"), ("disk writes", "written_mb_per_min", "MB/min")):
        rows = ", ".join(f"{name} {value:g}" for name, value in list(programs.get(key, {}).items())[:6])
        print(f"  {title} ({unit}): {rows or 'nothing over the floor'}")
    print("  starting processes:")
    for spawner in spawns["spawners"][:6]:
        mix = ", ".join(f"{n} {name}" for name, n in list(spawner["programs"].items())[:3])
        print(f"    {spawner['per_second']:6.2f}/s  {spawner['who']}  ({mix})")


def show_findings(findings: list[diagnose.Finding]) -> None:
    if not findings:
        print("Nothing got worse.")
    for finding in findings:
        print(finding.line())


def _recent(rows: list[dict], window: float) -> list[dict]:
    if not rows:
        return []
    return [row for row in rows if row["at"] >= rows[-1]["at"] - window]


def cmd_trace(args) -> int:
    traced = tracing.trace(args.seconds)
    if args.json:
        print(json.dumps({"seconds": traced.seconds, "started": traced.started, "per_second": round(traced.per_second, 2),
                          "by_program": traced.by_program, "spawners": [asdict(s) for s in traced.spawners],
                          "commands": traced.commands}))
    else:
        show_trace(traced)
    return 0


def cmd_sample(args) -> int:
    row = sample.take(args.seconds)
    if not args.no_save:
        sample.save(row)
    print(json.dumps(row)) if args.json else show_sample(row)
    return 0


def cmd_report(args) -> int:
    rows = _recent(sample.load(), args.since)
    if not rows:
        print("No samples yet. Run `culprit sample` (or `culprit watch`) first.", file=sys.stderr)
        return 1
    findings = diagnose.diagnose(rows)
    if args.json:
        print(json.dumps({"samples": len(rows), "latest": rows[-1], "findings": diagnose.as_dicts(findings)}))
    else:
        print(f"{len(rows)} samples over {(rows[-1]['at'] - rows[0]['at']) / 60:.0f} minutes\n")
        show_sample(rows[-1])
        print()
        show_findings(findings)
    return 2 if any(f.level == "worse" for f in findings) else 0


def cmd_watch(args) -> int:
    try:
        while True:
            row = sample.take(args.seconds)
            sample.save(row)
            show_sample(row)
            show_findings(diagnose.diagnose(_recent(sample.load(), args.since)))
            print(flush=True)
            time.sleep(max(0.0, args.every - args.seconds))
    except KeyboardInterrupt:
        return 0


def cmd_log(args) -> int:
    if args.action == "rules":
        print("An unattended run may only:")
        for kind, meaning in actions.ALLOWED.items():
            print(f"  {kind:22s} {meaning}")
        print("\nIt may never do these; they need a person:")
        for kind, meaning in actions.FORBIDDEN.items():
            print(f"  {kind:22s} {meaning}")
        return 0
    if args.action == "add":
        entry = {"stage": args.stage, "kind": args.kind, "target": args.target, "why": args.why, "change": args.change,
                 "undo": args.undo, "before": args.before, "after": args.after, "unit": args.unit}
        try:
            row = actions.add(entry, automatic=not args.approved)
        except actions.Refused as refusal:
            print(f"culprit: refused: {refusal}", file=sys.stderr)
            return 4
        print(json.dumps(row) if args.json else actions.line(row))
        return 0
    rows = actions.load()
    if args.json:
        print(json.dumps(rows))
    elif not rows:
        print("Nothing has been changed in culprit's name.")
    else:
        print("\n".join(actions.line(row) for row in rows))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="culprit", description=__doc__.strip().splitlines()[0],
                                     epilog=__doc__.split("\n", 2)[2], formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"culprit {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    def add(name, run, text):
        sub = commands.add_parser(name, help=text)
        sub.add_argument("-s", "--seconds", type=_seconds, default=30.0, help="how long to watch (default 30)")
        sub.add_argument("--json", action="store_true", help="print JSON")
        sub.set_defaults(run=run)
        return sub

    add("trace", cmd_trace, "who is starting processes")
    add("sample", cmd_sample, "one full sample, kept in the history").add_argument(
        "--no-save", action="store_true", help="do not add it to the history")
    for name, run, text in (("report", cmd_report, "what got worse, and who is behind it"),
                            ("watch", cmd_watch, "sample and report on a timer")):
        sub = add(name, run, text)
        sub.add_argument("--since", type=_seconds, default=6 * 3600.0, help="how far back to compare (default 6h)")
        if name == "watch":
            sub.add_argument("--every", type=_seconds, default=1200.0, help="time between samples (default 20m)")
    log = commands.add_parser("log", help="what was changed in culprit's name, and how to undo it")
    log.add_argument("action", nargs="?", choices=("show", "add", "rules"), default="show")
    log.add_argument("--stage", choices=actions.STAGES, help="planned, applied, verified, reverted or skipped")
    log.add_argument("--kind", help="the kind of optimisation (see `culprit log rules`)")
    log.add_argument("--target", help="what was changed: a file, a setting, a process")
    log.add_argument("--why", help="the finding behind it")
    log.add_argument("--change", help="what was done, in a sentence")
    log.add_argument("--undo", help="how to reverse it (required when applied)")
    log.add_argument("--before", type=float, help="the reading before")
    log.add_argument("--after", type=float, help="the reading after")
    log.add_argument("--unit", default="a second", help="what the readings count (default: a second)")
    log.add_argument("--approved", action="store_true", help="a person approved this action, so the automatic-only rules do not apply")
    log.add_argument("--json", action="store_true", help="print JSON")
    log.set_defaults(run=cmd_log)
    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except procs.Unsupported as problem:
        print(f"culprit: {problem}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
