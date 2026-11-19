"""What each platform's readers make of the kernel's own formats, on made-up input. These run anywhere."""
import struct

from culprit import _linux, _macos, label
from culprit.readings import CpuTicks, Memory, Usage


def test_procargs_and_rusage_parsers_read_their_layouts():
    # Arrange: argc 2, the path, padding, two arguments, then the environment
    raw = struct.pack("i", 2) + b"/bin/echo\0\0\0echo\0hello\0SECRET=1\0"
    counters = [0] * 18
    counters[0], counters[1], counters[7], counters[17] = 2_000_000_000, 1_000_000_000, 4096, 512
    # Act / Assert: the environment is never returned
    assert _macos.parse_procargs(raw) == ["echo", "hello"]
    assert _macos.parse_rusage(b"\0" * 16 + struct.pack("18Q", *counters)) == Usage(3.0, 4096, 512)


def test_stat_is_read_past_a_name_holding_spaces_and_brackets():
    # Arrange: the name is whatever the program set, so only the last bracket ends it
    raw = b"4242 (tmux: server (1)) S 77 4242 4242 0 -1 4194304 500 0 0 0 250 50 0 0 20 0 1 0 12345 1 2\n"
    # Act / Assert: the name, the parent, and user plus kernel time in clock ticks
    assert _linux.parse_stat(raw) == ("tmux: server (1)", 77, 300)


def test_a_stat_cut_short_is_unreadable_not_a_guess():
    assert _linux.parse_stat(b"") is None
    assert _linux.parse_stat(b"4242 (sh") is None
    assert _linux.parse_stat(b"4242 (sh) S 77") is None


def test_a_command_line_is_split_on_nul_and_an_empty_one_is_none():
    assert _linux.parse_cmdline(b"git\0-C\0/h/me/repo\0status\0") == ["git", "-C", "/h/me/repo", "status"]
    assert _linux.parse_cmdline(b"sh\0-c\0\0x\0") == ["sh", "-c", "", "x"]             # an empty argument is kept
    # a rewritten title is one run of text, padded: split on its spaces so each word can be redacted
    assert _linux.parse_cmdline(b"/usr/bin/worker --token private-value\0\0\0") == ["/usr/bin/worker", "--token", "private-value"]
    assert _linux.parse_cmdline(b"") is None                                           # a kernel thread, or a zombie


def test_footprint_is_private_memory_in_ram_and_in_swap():
    # Arrange
    status = (b"Name:\tnode\nVmRSS:\t  900000 kB\nRssAnon:\t  700000 kB\nRssFile:\t  150000 kB\n"
              b"RssShmem:\t   50000 kB\nVmSwap:\t  300000 kB\n")
    # Act / Assert: mapped files and shared memory are left out, since many processes share them
    assert _linux.parse_footprint(status) == (700000 + 300000) * 1024
    # an older kernel does not split resident memory, so all of it counts
    assert _linux.parse_footprint(b"VmRSS:\t     100 kB\nVmSwap:\t      20 kB\n") == 120 * 1024
    # a kernel thread has no memory of its own: it is not a program
    assert _linux.parse_footprint(b"Name:\tkworker/0:1\nState:\tI (idle)\n") is None


def test_bytes_written_leave_out_what_was_deleted_before_it_reached_the_disk():
    io = b"rchar: 9\nwchar: 9000\nread_bytes: 4096\nwrite_bytes: 8192\ncancelled_write_bytes: 4096\n"
    assert _linux.parse_written(io) == 4096
    # a process that deleted what others wrote reads below zero; 20 more bytes still show as 20 more
    before = _linux.parse_written(b"write_bytes: 10\ncancelled_write_bytes: 50\n")
    after = _linux.parse_written(b"write_bytes: 30\ncancelled_write_bytes: 50\n")
    assert (before, after, after - before) == (-40, -20, 20)


def test_the_cpu_line_counts_interrupts_as_kernel_time_and_waiting_for_io_as_idle():
    # Arrange: user nice system idle iowait irq softirq steal guest guest_nice
    raw = b"cpu  100 20 30 1000 50 4 6 0 0 0\ncpu0 50 10 15 500 25 2 3 0 0 0\n"
    # Act / Assert
    assert _linux.parse_cpu(raw) == CpuTicks(user=120, system=40, idle=1050)
    # a line that is not what it should be gives no reading, not a wrong one or a crash
    assert _linux.parse_cpu(b"cpu  100 20 thirty 1000\n") == CpuTicks(user=0, system=0, idle=0)


def test_memory_is_what_can_be_used_not_what_is_empty():
    # Arrange: nearly all "free" memory on Linux is cache the kernel hands back when asked
    meminfo = (b"MemTotal:       64092432 kB\nMemFree:          710284 kB\nMemAvailable:   50598676 kB\n"
               b"SwapTotal:       8388608 kB\nSwapFree:        6291456 kB\nZswap:           1048576 kB\n"
               b"Zswapped:        3145728 kB\n")
    # Act: one zram device holding 1 GB in half a GB of RAM
    memory = _linux.parse_memory(meminfo, pressure=1, zram=[(2**30, 2**29)])
    # Assert
    assert memory == Memory(free_gb=48.3, compressed_gb=1.5, stored_gb=4.0, swap_used_gb=2.0, swap_total_gb=8.0,
                            pressure=1)


def test_a_zram_device_reports_what_it_holds_and_the_ram_it_uses():
    # orig_data_size compr_data_size mem_used_total mem_limit mem_used_max same_pages pages_compacted ...
    assert _linux.parse_zram(b"1073741824 268435456 536870912 0 536870912 0 0 0 0\n") == (1073741824, 536870912)
    assert _linux.parse_zram(b"") is None
    assert _linux.parse_zram(b"not three numbers at all") is None


def test_pressure_is_given_on_the_scale_macos_uses():
    line = "some avg10={:.2f} avg60=0.00 avg300=0.00 total=0\nfull avg10={:.2f} avg60=0.00 avg300=0.00 total=0\n"
    assert _linux.parse_pressure(line.format(0.0, 0.0).encode()) == 1
    assert _linux.parse_pressure(line.format(12.5, 3.0).encode()) == 2    # some task waited for memory
    assert _linux.parse_pressure(line.format(60.0, 30.0).encode()) == 4   # every task waited at once
    assert _linux.parse_pressure(None) == 0                               # this kernel keeps no figures
    assert _linux.parse_pressure(b"not what was expected") == 0


def test_a_process_that_cannot_be_followed_is_named_by_the_kernels_short_name():
    assert _linux.parse_name(["/usr/sbin/sshd", "-D"], "sshd") == "sshd"
    assert _linux.parse_name(["postgres:", "18/main:", "writer"], "postgres") == "postgres"
    assert _linux.parse_name(None, "kworker/0:1") is None  # no command line: a kernel thread, or gone


def test_a_name_the_process_chose_for_itself_cannot_carry_a_secret_out():
    # Arrange: a title rewritten to hold a token, and a short name set to one
    words = _linux.parse_cmdline(b"/usr/bin/worker --token private-value\0")
    # Act
    name = _linux.parse_name(words, "worker")
    # Assert: the program is the kernel's name for it, and the line that is printed hides the value
    assert name == "worker" and label.program(name) == "worker"
    assert label.describe(name, words, home="/h/me") == f"worker --token {label.HIDDEN}"
    assert _linux.parse_name(["token=abc123"], "token=abc123") == f"token={label.HIDDEN}"
    # a first word made to look like a path is not believed, with a space in it or without
    assert _linux.parse_name(["/opt/x --token private-value", "run"], "x") == "x"
    assert _linux.parse_name(["/x/PLANTEDSECRETabc123xyz", "run"], "worker") == "worker"
