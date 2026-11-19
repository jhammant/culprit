from culprit.diagnose import diagnose


def sample(at=0, spawners=(), per_second=5.0, swap=2.0, pressure=1, free_disk=200.0, idle=60.0, system=10.0, memory=None,
           written=None):
    return {"at": at, "cpu": {"user": 100 - idle - system, "system": system, "idle": idle},
            "memory": {"free_gb": 8.0, "compressed_gb": 10.0, "stored_gb": 20.0, "swap_used_gb": swap,
                       "swap_total_gb": 16.0, "pressure": pressure},
            "disk": {"free_gb": free_disk, "total_gb": 1000.0},
            "spawns": {"per_second": per_second,
                       "spawners": [{"who": who, "pid": 1, "per_second": rate, "programs": {"ps": 9}} for who, rate in spawners]},
            "by_program": {"cpu_cores": {"Editor.app": 1.5}, "memory_gb": memory or {}, "written_mb_per_min": written or {}}}


def test_a_quiet_machine_has_no_findings():
    assert diagnose([sample(0), sample(600)]) == []


def test_one_busy_spawner_is_watched_and_a_sustained_one_is_worse():
    # Arrange
    burst = [sample(0), sample(600, spawners=[("python3 poller.py", 6.0)])]
    habit = [sample(t * 600, spawners=[("python3 poller.py", 6.0)]) for t in range(3)]
    # Act
    once, thrice = diagnose(burst), diagnose(habit)
    # Assert
    assert [(f.level, f.suspect) for f in once] == [("watch", "python3 poller.py")]
    assert thrice[0].level == "worse" and "3 samples running" in thrice[0].what and "ps" in thrice[0].evidence


def test_swap_growth_names_the_programs_that_grew():
    # Arrange
    before = sample(0, swap=2.0, memory={"node (Models.app)": 16.0, "Browser.app": 9.0})
    after = sample(600, swap=4.5, memory={"node (Models.app)": 38.0, "Browser.app": 9.4})
    # Act
    findings = diagnose([before, after])
    # Assert
    swap = next(f for f in findings if "Swap grew" in f.what)
    assert swap.level == "worse" and swap.suspect.startswith("node (Models.app) +22")
    assert any(f.level == "watch" and f.suspect == "node (Models.app)" for f in findings)


def test_a_falling_disk_says_when_it_fills_and_who_is_writing():
    # Arrange: 40 GB gone in two hours with 60 GB left
    rows = [sample(0, free_disk=100.0), sample(3600, free_disk=80.0),
            sample(7200, free_disk=60.0, written={"Builder.app": 900.0})]
    # Act
    finding = next(f for f in diagnose(rows) if "Free disk" in f.what)
    # Assert
    assert "fell 40 GB in 2.0 h" in finding.what and "full in 3 h" in finding.evidence
    assert finding.suspect.startswith("Builder.app 900")


def test_churn_with_high_kernel_time_and_a_busy_cpu_are_reported():
    # Act
    findings = diagnose([sample(0), sample(600, per_second=40.0, system=30.0, idle=10.0)])
    # Assert
    assert any("kernel time" in f.what for f in findings)
    assert any("idle" in f.what and "Editor.app" in f.suspect for f in findings)


def test_memory_pressure_and_a_spawner_that_stopped():
    # Arrange
    rows = [sample(0, spawners=[("python3 poller.py", 6.0)]), sample(600, pressure=2, memory={"Browser.app": 12.0})]
    # Act
    findings = diagnose(rows)
    # Assert: worse first, better last
    assert findings[0].level == "worse" and "memory pressure" in findings[0].what and "Browser.app" in findings[0].suspect
    assert findings[-1].level == "better" and findings[-1].suspect == "python3 poller.py"


def test_memory_pressure_is_reported_by_the_system_the_sample_came_from():
    # Arrange: rows written before samples named their system are from macOS
    old, linux = sample(600, pressure=2), {**sample(600, pressure=2), "os": "Linux"}
    # Act / Assert
    assert diagnose([sample(0), old])[0].what == "macOS reports memory pressure."
    assert diagnose([sample(0), linux])[0].what == "Linux reports memory pressure."
