import json
import stat

import pytest

from culprit import sample
from culprit.procs import Usage


@pytest.mark.parametrize("path, parent, expected", [
    ("/Applications/Browser.app/Contents/Frameworks/Helper.app/Contents/MacOS/Helper", None, "Browser.app"),
    ("/Users/me/.models/bin/node", "/Applications/Models.app/Contents/MacOS/Models", "node (Models.app)"),
    ("/opt/py/Python.app/Contents/MacOS/Python", "/bin/zsh", "Python"),
    ("/Users/me/.local/share/agent/versions/2.1.3", "/bin/zsh", "agent"),
    ("/Library/Developer/CoreSimulator/Volumes/x/usr/libexec/thing", None, "iOS Simulator"),
    ("/usr/bin/git", "/Applications/Terminal.app/Contents/MacOS/Terminal", "git (Terminal.app)"),
    (None, None, "(unreadable)"),
])
def test_family_groups_a_process_with_the_app_it_belongs_to(path, parent, expected):
    assert sample.family(path, parent) == expected


def test_by_family_sums_deltas_over_the_window_and_memory_as_it_stands():
    # Arrange: two helpers of one app over 10 s, and a process that started during the window
    gb, mb = 2**30, 2**20
    before = {1: ("Browser.app", Usage(10.0, 1 * gb, 0)), 2: ("Browser.app", Usage(5.0, 2 * gb, 100 * mb))}
    after = {1: ("Browser.app", Usage(14.0, 1 * gb, 0)), 2: ("Browser.app", Usage(11.0, 3 * gb, 150 * mb)),
             3: ("git", Usage(0.2, 0, 0))}
    # Act
    got = sample.by_family(before, after, seconds=10.0, cores=8)
    # Assert: 10 CPU-seconds in 10 s is one core; 50 MB in 10 s is 300 MB a minute; new processes add memory only
    assert got == {"cpu_cores": {"Browser.app": 1.0}, "memory_gb": {"Browser.app": 4.0},
                   "written_mb_per_min": {"Browser.app": 300.0}}


def test_history_is_private_keeps_order_and_survives_a_torn_line(tmp_path):
    # Arrange
    path = tmp_path / "state" / "history.jsonl"
    sample.save({"at": 1, "n": "first"}, path)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write('{"at": 2, "n": "to')  # a crash mid-write
    # Act
    sample.save({"at": 3, "n": "third"}, path)
    # Assert
    assert [row["n"] for row in sample.load(path)] == ["first", "third"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert all(json.loads(line) for line in path.read_text().splitlines())


def test_history_is_capped(tmp_path, monkeypatch):
    # Arrange
    monkeypatch.setattr(sample, "KEEP_SAMPLES", 3)
    path = tmp_path / "history.jsonl"
    # Act
    for at in range(5):
        sample.save({"at": at}, path)
    # Assert
    assert [row["at"] for row in sample.load(path)] == [2, 3, 4]


def test_a_program_that_deleted_what_others_wrote_is_still_charged_what_it_wrote_since():
    # Arrange: its running total is below zero at both readings, and 60 MB higher at the second
    mb = 2**20
    before = {1: ("Builder", Usage(1.0, 0, -40 * mb))}
    after = {1: ("Builder", Usage(1.0, 0, 20 * mb))}
    # Act
    got = sample.by_family(before, after, seconds=10.0, cores=8)
    # Assert
    assert got["written_mb_per_min"] == {"Builder": 360.0}
