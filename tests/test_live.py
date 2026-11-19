"""Checks against the real machine: what culprit reads is what the kernel says."""
import json
import os
import re
import subprocess
import sys
import time

import pytest

from culprit import cli, procs, system

pytestmark = pytest.mark.skipif(procs.OS_NAME is None, reason="culprit reads macOS and Linux")
macos = pytest.mark.skipif(sys.platform != "darwin", reason="a check against macOS's own tools")
linux = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="a check against /proc")


def test_this_process_reads_as_itself():
    me = os.getpid()
    assert me in procs.pids()
    assert procs.parent(me) == os.getppid()
    assert os.path.basename(procs.path(me)).lower().startswith("python")
    assert procs.argv(me)[0].lower().find("python") >= 0
    used = procs.usage(me)
    assert used.cpu_seconds > 0 and used.footprint_bytes > 1_000_000


def test_a_child_reads_as_what_was_started_and_as_nothing_once_it_has_gone():
    # Arrange
    code = "import time; time.sleep(30)"
    child = subprocess.Popen([sys.executable, "-c", code])
    try:
        # Act / Assert: its parent, its program and its command line
        assert child.pid in procs.pids()
        assert procs.parent(child.pid) == os.getpid()
        assert os.path.basename(procs.path(child.pid)).lower().startswith("python")
        assert procs.argv(child.pid)[1:] == ["-c", code]
    finally:
        child.kill()
        child.wait()
    # Assert: a process that has gone is unknown, never guessed
    assert procs.path(child.pid) is None and procs.argv(child.pid) is None and procs.usage(child.pid) is None


def test_a_root_owned_process_still_has_a_parent():
    # the first process is root's; its parent is the kernel (0)
    assert procs.parent(1) == 0


@linux
def test_another_users_process_is_named_and_measured_without_its_program_link():
    # the first process is root's: the link to its program is closed to us, the rest is not
    assert procs.path(1) and procs.argv(1)
    assert procs.usage(1).footprint_bytes > 0


@macos
def test_memory_matches_vm_stat():
    # Arrange: what Apple's own tool says
    out = subprocess.run(["vm_stat"], capture_output=True, text=True, check=True).stdout
    page = int(re.search(r"page size of (\d+)", out).group(1))
    pages = lambda name: int(re.search(name + r":\s+(\d+)", out).group(1)) * page / 2**30
    # Act
    memory = system.memory()
    # Assert: the same counters, a moment apart
    assert memory.compressed_gb == pytest.approx(pages("Pages occupied by compressor"), abs=2.0)
    assert memory.stored_gb == pytest.approx(pages("Pages stored in compressor"), abs=3.0)
    assert memory.pressure in (1, 2, 4) and memory.swap_total_gb >= memory.swap_used_gb >= 0


@linux
def test_memory_matches_meminfo():
    # Arrange: the kernel's own table, in kB
    with open("/proc/meminfo", encoding="ascii") as handle:
        kb = {line.split(":")[0]: int(line.split()[1]) for line in handle}
    # Act
    memory = system.memory()
    # Assert: free is what can be handed out, which is never less than what is empty
    assert memory.free_gb == pytest.approx(kb["MemAvailable"] / 2**20, abs=2.0)
    assert memory.free_gb >= kb["MemFree"] / 2**20 - 2.0
    assert memory.swap_total_gb == pytest.approx(kb["SwapTotal"] / 2**20, abs=0.1)
    assert memory.pressure in (0, 1, 2, 4) and memory.swap_total_gb >= memory.swap_used_gb >= 0
    assert memory.stored_gb >= memory.compressed_gb >= 0


def test_cpu_and_disk_are_sane():
    before = system.cpu_ticks()
    start = time.monotonic()
    while time.monotonic() - start < 0.1:  # long enough for the kernel's clock to tick
        sum(i * i for i in range(10_000))
    share = system.cpu_share(before, system.cpu_ticks())
    assert 99.0 <= share["user"] + share["system"] + share["idle"] <= 101.0
    disk = system.disk()
    assert 0 < disk.free_gb < disk.total_gb


def test_trace_command_prints_json(capsys):
    # Act
    code = cli.main(["trace", "-s", "1", "--json"])
    # Assert
    out = capsys.readouterr().out
    assert code == 0 and '"spawners"' in out and '"per_second"' in out


def test_a_sample_says_which_system_it_was_taken_on(capsys):
    # Act
    code = cli.main(["sample", "-s", "1", "--no-save", "--json"])
    # Assert
    row = json.loads(capsys.readouterr().out)
    assert code == 0 and row["os"] == procs.OS_NAME and row["processes"] > 0
    assert set(row["memory"]) == {"free_gb", "compressed_gb", "stored_gb", "swap_used_gb", "swap_total_gb", "pressure"}
    assert set(row["by_program"]) == {"cpu_cores", "memory_gb", "written_mb_per_min"}
