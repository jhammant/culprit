"""What got worse, and who is behind it.

Findings come from comparing the newest sample with the ones before it. Every finding names a
suspect and carries the numbers it rests on, so a person or an agent can check it before acting.
The rules are deliberately few and plain: a wrong accusation costs more than a missed one.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

LIMITS = {
    "spawner_per_s": 3.0,      # one long-lived process starting this many processes a second
    "spawns_per_s": 20.0,      # the whole machine
    "sustained_samples": 3,    # in this many samples running, a spawner is a habit, not a burst
    "swap_growth_gb": 1.0,     # since the sample before
    "memory_growth_gb": 2.0,   # one program, since the sample before
    "disk_fall_gb": 5.0,       # across the samples looked at
    "idle_percent": 15.0,      # CPU left idle
    "system_percent": 25.0,    # CPU spent in the kernel: the mark of process churn
}


@dataclass
class Finding:
    level: str        # "worse", "watch" or "better"
    what: str
    suspect: str
    evidence: str

    def line(self) -> str:
        return f"[{self.level}] {self.what} Suspect: {self.suspect}. ({self.evidence})"


def _spawners(sample: dict) -> dict[str, float]:
    return {s["who"]: s["per_second"] for s in sample.get("spawns", {}).get("spawners", [])}


def _mix(sample: dict, who: str) -> str:
    for spawner in sample.get("spawns", {}).get("spawners", []):
        if spawner["who"] == who:
            return ", ".join(f"{n} {name}" for name, n in list(spawner.get("programs", {}).items())[:3])
    return ""


def _grew(before: dict, after: dict, key: str) -> list[tuple[str, float]]:
    """Programs whose reading under `key` rose between two samples, largest rise first."""
    old, new = before.get("by_program", {}).get(key, {}), after.get("by_program", {}).get(key, {})
    return sorted(((name, round(value - old.get(name, 0.0), 1)) for name, value in new.items()
                   if value - old.get(name, 0.0) > 0), key=lambda pair: -pair[1])


def _top(sample: dict, key: str, unit: str, count: int = 3) -> str:
    rows = list(sample.get("by_program", {}).get(key, {}).items())[:count]
    return ", ".join(f"{name} {value} {unit}" for name, value in rows) or "nothing this user can read"


def diagnose(history: list[dict], limits: dict | None = None) -> list[Finding]:
    """Findings for the newest sample in `history` (oldest first), worst first."""
    limit = {**LIMITS, **(limits or {})}
    if not history:
        return []
    now, earlier = history[-1], history[:-1]
    previous = earlier[-1] if earlier else None
    out: list[Finding] = []

    # Process churn: who is starting processes.
    spawners = _spawners(now)
    for who, rate in spawners.items():
        if rate < limit["spawner_per_s"]:
            continue
        run = 1
        for old in reversed(earlier):
            if _spawners(old).get(who, 0.0) < limit["spawner_per_s"]:
                break
            run += 1
        habit = run >= limit["sustained_samples"]
        out.append(Finding("worse" if habit else "watch",
                           f"One process is starting {rate:g} processes a second" + (f", for {run} samples running." if habit else "."),
                           who, f"mostly {_mix(now, who) or 'short-lived children'}"))
    rate = now.get("spawns", {}).get("per_second", 0.0)
    cpu = now.get("cpu", {})
    if rate >= limit["spawns_per_s"] and cpu.get("system", 0.0) >= limit["system_percent"]:
        out.append(Finding("worse", f"{rate:g} processes a second are being started and {cpu['system']:g}% of the CPU is kernel time.",
                           next(iter(spawners), "no single process"), "starting and ending processes is kernel work"))

    # Memory.
    memory = now.get("memory", {})
    if previous:
        swap = round(memory.get("swap_used_gb", 0.0) - previous.get("memory", {}).get("swap_used_gb", 0.0), 1)
        grew = _grew(previous, now, "memory_gb")
        if swap >= limit["swap_growth_gb"]:
            suspect = ", ".join(f"{name} +{gb} GB" for name, gb in grew[:3]) or "no program this user can read grew"
            out.append(Finding("worse", f"Swap grew {swap:g} GB to {memory.get('swap_used_gb'):g} GB since the last sample.",
                               suspect, f"memory pressure level {memory.get('pressure')}, {memory.get('free_gb'):g} GB free"))
        for name, gb in grew:
            if gb >= limit["memory_growth_gb"]:
                out.append(Finding("watch", f"{name} is using {gb:g} GB more memory than at the last sample.", name,
                                   f"now {now['by_program']['memory_gb'][name]:g} GB"))
    if memory.get("pressure", 1) >= 2:
        out.append(Finding("worse", f"{now.get('os', 'macOS')} reports memory pressure.", _top(now, "memory_gb", "GB"),
                           f"level {memory['pressure']}, swap {memory.get('swap_used_gb'):g} of {memory.get('swap_total_gb'):g} GB"))

    # Disk: the fall across the samples, and how long it lasts at that rate.
    if earlier:
        first = next((s for s in earlier if s.get("disk")), None)
        if first and now.get("disk"):
            fall = round(first["disk"]["free_gb"] - now["disk"]["free_gb"], 1)
            hours = (now["at"] - first["at"]) / 3600
            if fall >= limit["disk_fall_gb"] and hours > 0:
                left = now["disk"]["free_gb"] / (fall / hours)
                out.append(Finding("worse", f"Free disk fell {fall:g} GB in {hours:.1f} h to {now['disk']['free_gb']:g} GB.",
                                   _top(now, "written_mb_per_min", "MB/min"), f"at this rate it is full in {left:.0f} h"))

    # CPU.
    if cpu.get("idle", 100.0) < limit["idle_percent"]:
        out.append(Finding("worse", f"Only {cpu['idle']:g}% of the CPU is idle.", _top(now, "cpu_cores", "cores"),
                           f"user {cpu.get('user'):g}%, kernel {cpu.get('system'):g}%"))

    # What got better: a spawner that was over the limit and is not any more.
    if previous:
        for who, was in _spawners(previous).items():
            if was >= limit["spawner_per_s"] and spawners.get(who, 0.0) < limit["spawner_per_s"] / 2:
                out.append(Finding("better", f"A process that was starting {was:g} processes a second has stopped.", who,
                                   f"now {spawners.get(who, 0.0):g} a second"))

    order = {"worse": 0, "watch": 1, "better": 2}
    return sorted(out, key=lambda finding: order[finding.level])


def as_dicts(findings: list[Finding]) -> list[dict]:
    return [asdict(finding) for finding in findings]
