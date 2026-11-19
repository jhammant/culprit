from culprit import trace as tracing
from culprit.trace import Proc


def proc(pid, ppid, path, *argv):
    return Proc(pid=pid, ppid=ppid, path=path, argv=list(argv) or None)


def test_new_processes_are_charged_to_the_ancestor_that_was_already_running():
    # Arrange: a poller that starts a shell, which starts ps twice; an app that starts one helper
    base = {1: proc(1, 0, "/sbin/launchd", "launchd"),
            10: proc(10, 1, "/usr/bin/python3", "python3", "/h/me/poller.py"),
            20: proc(20, 1, "/Applications/Thing.app/Contents/MacOS/Thing", "Thing")}
    known = {**base,
             100: proc(100, 10, "/bin/sh", "sh", "-c", "ps -p 1"),
             101: proc(101, 100, "/bin/ps", "ps", "-p", "1"),
             102: proc(102, 100, "/bin/ps", "ps", "-p", "1"),
             200: proc(200, 20, "/usr/bin/helper", "helper")}
    # Act
    traced = tracing.attribute(base, known, seconds=2.0, home="/h/me", own_pid=999)
    # Assert
    assert traced.started == 4 and traced.per_second == 2.0
    assert [(s.who, s.pid, s.count) for s in traced.spawners] == [("python3 poller.py", 10, 3), ("Thing.app", 20, 1)]
    assert traced.spawners[0].programs == {"ps": 2, "sh": 1}
    assert traced.by_program == {"ps": 2, "sh": 1, "helper": 1}


def test_a_helper_is_named_with_the_app_that_started_it():
    # Arrange: an app's inline-script helper, running since before the trace, starts ps
    base = {30: proc(30, 1, "/Applications/Models.app/Contents/MacOS/Models", "Models"),
            31: proc(31, 30, "/h/me/.models/node", "node", "-e", "watch()")}
    known = {**base, 400: proc(400, 31, "/bin/ps", "ps")}
    # Act
    traced = tracing.attribute(base, known, seconds=1.0, home="/h/me", own_pid=999)
    # Assert
    assert [s.who for s in traced.spawners] == ["node -e (inline code) (started by Models.app)"]


def test_what_launchd_starts_is_named_by_the_job_not_by_launchd():
    # Arrange: a scheduled job that ran twice, each run starting a child; and a one-off
    base = {1: proc(1, 0, "/sbin/launchd", "launchd")}
    known = {**base,
             100: proc(100, 1, "/bin/bash", "bash", "/h/me/jobs/sync.sh"), 101: proc(101, 100, "/usr/bin/rsync", "rsync"),
             200: proc(200, 1, "/bin/bash", "bash", "/h/me/jobs/sync.sh"), 201: proc(201, 200, "/usr/bin/rsync", "rsync"),
             300: proc(300, 1, "/usr/libexec/helperd", "helperd")}
    # Act
    traced = tracing.attribute(base, known, seconds=1.0, home="/h/me", own_pid=999)
    # Assert
    assert [(s.who, s.pid, s.count) for s in traced.spawners] == [
        ("bash sync.sh (a job launchd starts)", None, 4), ("helperd (a job launchd starts)", None, 1)]


def test_a_process_whose_parent_was_never_seen_is_an_orphan_not_a_guess():
    # Arrange: pid 300's parent 250 came and went between two looks
    base = {2: proc(2, 0, "/usr/bin/other", "other")}
    known = {**base, 300: proc(300, 250, None)}
    # Act
    traced = tracing.attribute(base, known, seconds=1.0, own_pid=999)
    # Assert
    assert [(s.who, s.pid) for s in traced.spawners] == [(tracing.ORPHAN, None)]
    assert traced.by_program == {tracing.UNREAD: 1}


def test_what_culprit_starts_itself_is_left_out():
    # Arrange: culprit is pid 50 and has a child
    base = {50: proc(50, 1, "/usr/bin/python3", "python3", "-m", "culprit")}
    known = {**base, 500: proc(500, 50, "/bin/echo", "echo")}
    # Act
    traced = tracing.attribute(base, known, seconds=1.0, own_pid=50)
    # Assert
    assert traced.started == 0 and traced.spawners == []


def test_observe_reads_a_new_process_again_after_it_execs():
    # Arrange: pid 7 appears as a copy of its parent, then becomes git; time moves 1 s a look
    tables = [{1}, {1, 7}, {1, 7}, {1}]
    looks = {"n": 0}
    reads = {7: [proc(7, 1, "/bin/zsh", "zsh"), proc(7, 1, "/usr/bin/git", "git", "status")]}

    def list_pids():
        looks["n"] += 1
        return tables[min(looks["n"] - 1, len(tables) - 1)]

    def read(pid):
        return proc(1, 0, "/sbin/launchd") if pid == 1 else (reads[7].pop(0) if len(reads[7]) > 1 else reads[7][0])

    now = {"t": 0.0}

    def clock():
        now["t"] += 0.5
        return now["t"]

    # Act
    base, known, elapsed = tracing.observe(3.0, list_pids=list_pids, read_proc=read, clock=clock, sleep=lambda s: None)
    # Assert
    assert set(base) == {1} and known[7].path == "/usr/bin/git" and known[7].argv == ["git", "status"]
    assert elapsed >= 3.0


def test_what_a_users_own_service_manager_starts_is_named_by_the_job_too():
    # Arrange: Linux gives each logged-in user a service manager of their own, beside pid 1
    base = {1: proc(1, 0, "/sbin/init", "/sbin/init"),
            900: proc(900, 1, "/usr/lib/systemd/systemd", "/usr/lib/systemd/systemd", "--user")}
    known = {**base,
             100: proc(100, 900, "/usr/bin/bash", "bash", "/h/me/jobs/sync.sh"), 101: proc(101, 100, "/usr/bin/rsync", "rsync"),
             300: proc(300, 1, "/usr/sbin/logrotate", "logrotate")}
    # Act
    traced = tracing.attribute(base, known, seconds=1.0, home="/h/me", own_pid=999)
    # Assert
    assert [(s.who, s.pid, s.count) for s in traced.spawners] == [
        ("bash sync.sh (a job systemd starts)", None, 2), ("logrotate (a job init starts)", None, 1)]


def test_a_first_process_that_is_no_service_manager_is_named_like_any_other():
    # Arrange: in a container the first process is the program itself
    base = {1: proc(1, 0, "/usr/bin/python3", "python3", "/app/serve.py")}
    known = {**base, 50: proc(50, 1, "/usr/bin/git", "git", "status")}
    # Act
    traced = tracing.attribute(base, known, seconds=1.0, home="/h/me", own_pid=999)
    # Assert
    assert [(s.who, s.pid, s.count) for s in traced.spawners] == [("python3 serve.py", 1, 1)]


def test_the_kernels_own_threads_are_not_counted_but_what_the_kernel_runs_is():
    # Arrange: Linux lists kernel threads beside processes, as children of pid 2 with no command line
    base = {2: proc(2, 0, None), 10: proc(10, 1, "/usr/bin/python3", "python3", "/h/me/poller.py")}
    known = {**base, 700: proc(700, 2, None), 701: proc(701, 2, "/usr/sbin/modprobe", "modprobe", "-q", "fuse"),
             702: proc(702, 10, "/bin/ps", "ps")}
    # Act
    traced = tracing.attribute(base, known, seconds=1.0, home="/h/me", own_pid=999, kernel_parent=2)
    # Assert: the new kernel thread is left out; the program the kernel started is charged to the kernel
    assert traced.started == 2 and traced.by_program == {"modprobe": 1, "ps": 1}
    assert sorted((s.who, s.pid) for s in traced.spawners) == [(tracing.KERNEL, 2), ("python3 poller.py", 10)]


def test_a_first_process_that_cannot_be_read_is_still_the_service_manager():
    # Arrange
    base = {1: proc(1, 0, None)}
    known = {**base, 100: proc(100, 1, "/bin/bash", "bash", "/h/me/jobs/sync.sh")}
    # Act
    traced = tracing.attribute(base, known, seconds=1.0, home="/h/me", own_pid=999)
    # Assert
    assert [(s.who, s.pid, s.count) for s in traced.spawners] == [("bash sync.sh (a job pid 1 starts)", None, 1)]


def test_a_program_that_only_shares_a_service_managers_name_is_named_like_any_other():
    # Arrange: somebody's own `init --user`, which is not pid 1 and is not systemd
    base = {1: proc(1, 0, "/sbin/launchd", "launchd"),
            777: proc(777, 1, "/usr/local/bin/init", "init", "--user", "build-agent")}
    known = {**base, 900: proc(900, 777, "/usr/bin/git", "git", "fetch")}
    # Act
    traced = tracing.attribute(base, known, seconds=1.0, home="/h/me", own_pid=999)
    # Assert
    assert [(s.who, s.pid, s.count) for s in traced.spawners] == [("init --user build-agent", 777, 1)]


def test_busybox_init_is_a_service_manager_and_another_busybox_program_is_not():
    # Arrange: read as root, BusyBox's init is the program `busybox` run under the name init
    job = {100: proc(100, 1, "/bin/busybox", "crond", "-f"), 101: proc(101, 100, "/usr/bin/rsync", "rsync")}
    as_init = {1: proc(1, 0, "/bin/busybox", "init")}
    as_shell = {1: proc(1, 0, "/bin/busybox", "sh", "/entrypoint.sh")}
    # Act
    managed = tracing.attribute(as_init, {**as_init, **job}, seconds=1.0, home="/h/me", own_pid=999)
    plain = tracing.attribute(as_shell, {**as_shell, **job}, seconds=1.0, home="/h/me", own_pid=999)
    # Assert
    assert [(s.who, s.pid, s.count) for s in managed.spawners] == [("crond -f (a job init starts)", None, 2)]
    assert [(s.who, s.pid, s.count) for s in plain.spawners] == [("sh entrypoint.sh", 1, 2)]


def test_a_program_that_only_calls_itself_a_users_systemd_is_named_like_any_other():
    # Arrange: the first argument is whatever a process says it is; its program is not
    base = {1: proc(1, 0, "/usr/lib/systemd/systemd", "/sbin/init"),
            500: proc(500, 1, "/home/me/bin/poller", "systemd", "--user")}
    known = {**base, 600: proc(600, 500, "/bin/ps", "ps")}
    # Act
    traced = tracing.attribute(base, known, seconds=1.0, home="/h/me", own_pid=999)
    # Assert
    assert [(s.who, s.pid, s.count) for s in traced.spawners] == [("systemd --user", 500, 1)]
