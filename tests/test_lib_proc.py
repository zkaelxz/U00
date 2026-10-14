"""lib.proc: the one runner for long external commands. Real child
processes (python -c), no network; the POSIX cases need process groups."""
import logging
import os
import signal
import sys
import threading
import time

import pytest

from lib import proc as proc_run

posix = pytest.mark.skipif(os.name == "nt", reason="POSIX sessions")


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        with open(f"/proc/{pid}/status") as fh:      # killed but not yet reaped
            return "zombie" not in fh.read().lower()
    except OSError:
        return True


def _wait_dead(pid, seconds=5):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return False


def _tree_script(marker, same_group=True, parent_sleeps=60):
    """Prints 'started', records a grandchild's pid in `marker`. With
    same_group=False the grandchild has its own session (out of reach of a
    process-group kill) and keeps inheriting stdout."""
    session = "" if same_group else ", start_new_session=True"
    return ("import subprocess, sys, time\n"
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']"
            f"{session})\n"
            f"open({str(marker)!r}, 'w').write(str(p.pid))\n"
            "print('started', flush=True)\n"
            f"time.sleep({parent_sleeps})\n")


def _kill_marker(marker):
    try:
        os.kill(int(marker.read_text()), signal.SIGKILL)
    except (OSError, ValueError):
        pass


@posix
def test_cancel_kills_a_silent_child_tree_without_waiting_for_output(tmp_path):
    marker = tmp_path / "gc.pid"
    flag = threading.Event()
    threading.Timer(0.7, flag.set).start()
    script = ("import subprocess, sys, time\n"
              "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
              f"open({str(marker)!r}, 'w').write(str(p.pid))\n"
              "time.sleep(60)\n")             # never prints: only the cancel poll can notice
    t0 = time.monotonic()
    items = list(proc_run.stream_tree([sys.executable, "-c", script], 60.0,
                                             drain_seconds=2.0, cancel=flag.is_set))
    assert time.monotonic() - t0 < 15
    assert items[-1]["cancelled"] is True and items[-1]["timed_out"] is False
    assert _wait_dead(int(marker.read_text())), "grandchild survived the cancel"


@posix
def test_timeout_kills_the_whole_tree(tmp_path):
    marker = tmp_path / "gc.pid"
    items = list(proc_run.stream_tree([sys.executable, "-c", _tree_script(marker)], 1.0,
                                             drain_seconds=2.0))
    assert items[0] == {"line": "started"}
    assert items[-1]["timed_out"] is True and items[-1]["cancelled"] is False
    assert _wait_dead(int(marker.read_text()))


@posix
def test_a_grandchild_holding_the_pipe_cannot_hang_stream_tree(tmp_path):
    marker = tmp_path / "gc.pid"
    try:
        t0 = time.monotonic()
        items = list(proc_run.stream_tree(
            [sys.executable, "-c", _tree_script(marker, same_group=False, parent_sleeps=0)], 60.0,
            drain_seconds=1.0))
        assert time.monotonic() - t0 < 15
        assert items[-1] == {"returncode": 0, "timed_out": False, "cancelled": False}
    finally:
        _kill_marker(marker)


@posix
def test_a_grandchild_holding_the_pipe_cannot_hang_run_captured(tmp_path):
    marker = tmp_path / "gc.pid"
    try:
        t0 = time.monotonic()
        out = proc_run.run_captured(
            [sys.executable, "-c", _tree_script(marker, same_group=False, parent_sleeps=60)], 1.0,
            drain_seconds=1.0)
        assert time.monotonic() - t0 < 15
        assert out.timed_out is True and "started" in out.stdout
    finally:
        _kill_marker(marker)


@posix
def test_closing_the_generator_early_kills_the_child(tmp_path):
    marker = tmp_path / "gc.pid"
    gen = proc_run.stream_tree([sys.executable, "-c", _tree_script(marker)], 60.0,
                                      drain_seconds=2.0)
    assert next(gen) == {"line": "started"}
    gen.close()
    assert _wait_dead(int(marker.read_text()))


def test_run_captured_keeps_stdout_and_stderr_apart():
    script = ("import sys\nprint('out')\nprint('err', file=sys.stderr)\nsys.exit(3)\n")
    out = proc_run.run_captured([sys.executable, "-c", script], 30.0)
    assert (out.returncode, out.stdout.strip(), out.stderr.strip()) == (3, "out", "err")
    assert out.timed_out is False and out.cancelled is False


def test_run_captured_reports_the_child_pid_once_it_has_started():
    pids = []
    out = proc_run.run_captured([sys.executable, "-c", "import os; print(os.getpid())"], 30.0,
                                on_start=pids.append)
    assert pids == [int(out.stdout.strip())]


def test_run_captured_gives_the_child_no_stdin():
    out = proc_run.run_captured(
        [sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"], 30.0)
    assert out.stdout.strip() == "''"


def test_run_captured_reports_a_missing_program_as_oserror():
    with pytest.raises(OSError):
        proc_run.run_captured(["definitely-not-a-program-baihe"], 5.0)


def test_run_captured_keeps_only_the_newest_output(monkeypatch):
    monkeypatch.setattr(proc_run, "_CAPTURE_LIMIT_CHARS", 100)
    script = "for i in range(500):\n    print('line', i)\n"
    out = proc_run.run_captured([sys.executable, "-c", script], 30.0)
    assert "line 499" in out.stdout and "line 0\n" not in out.stdout
    assert len(out.stdout) < 400


@pytest.mark.skipif(os.name == "nt", reason="uses a shebang script as the fake venv python")
def test_throwaway_venv_probe_survives_a_non_utf8_child_locale(tmp_path, monkeypatch):
    import upgrade_check

    wanted = tmp_path / "张三" / "site-packages"
    fake = tmp_path / "fake_python"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        f"p = {str(wanted)!r}\n"
        "utf8 = os.environ.get('PYTHONUTF8') == '1' or os.environ.get('PYTHONIOENCODING') == 'utf-8'\n"
        "sys.stdout.buffer.write(p.encode('utf-8') if utf8 else p.encode('cp936'))\n"
    )
    fake.chmod(0o755)
    monkeypatch.setattr(upgrade_check, "_venv_python", lambda venv_dir: str(fake))

    venv_py, err = upgrade_check._make_throwaway_venv(
        str(tmp_path), "trial", sys.executable, ["/parent/site"])

    assert err is None
    assert (wanted / "_baihe_parent_env.pth").read_text(encoding="utf-8") == "/parent/site\n"


@posix
def test_stream_tree_kills_a_same_group_grandchild_still_holding_the_pipe(tmp_path):
    marker = tmp_path / "gc.pid"
    try:
        items = list(proc_run.stream_tree(
            [sys.executable, "-c", _tree_script(marker, parent_sleeps=0)], 60.0,
            drain_seconds=1.0))
        assert items[-1]["returncode"] == 0
        assert _wait_dead(int(marker.read_text())), "grandchild outlived stream_tree"
    finally:
        _kill_marker(marker)


def test_kill_tree_failure_does_not_raise_and_logs_only_the_type(monkeypatch, caplog):
    from lib import proc_kill

    def boom(*args):
        raise PermissionError("secret-token-in-message")

    class _Proc:
        pid = 4242
        kill = boom

    monkeypatch.setattr(proc_kill.os, "killpg", boom, raising=False)
    monkeypatch.setattr(proc_kill.subprocess, "run", boom)
    # applog.get_logger() turns propagation off for the process once any earlier test has
    # called it, and caplog listens on the root logger.
    monkeypatch.setattr(logging.getLogger("baihe"), "propagate", True)
    with caplog.at_level("WARNING", logger="baihe"):
        proc_kill.kill_tree(_Proc())
    assert "PermissionError" in caplog.text
    assert "secret-token-in-message" not in caplog.text


linux_pidfd = pytest.mark.skipif(not (os.path.isdir("/proc/self") and hasattr(os, "pidfd_open")),
                                 reason="identity checks and race-free kill need /proc and pidfd")


@linux_pidfd
def test_stop_if_same_kills_only_the_noted_process():
    import subprocess
    from lib import proc_kill
    a = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    b = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(61)"])
    try:
        ident_a = proc_kill.process_identity(a.pid)
        assert ident_a and ident_a != proc_kill.process_identity(b.pid)
        # b's pid with a's identity: the "pid reused" case.
        assert proc_kill.stop_if_same(b.pid, ident_a) == "gone"
        assert b.poll() is None
        assert proc_kill.stop_if_same(a.pid, ident_a) == "stopped"
        assert a.wait(timeout=5) is not None
    finally:
        for p in (a, b):
            if p.poll() is None:
                p.kill()
            p.wait()


def test_stop_if_same_never_targets_this_process_or_a_bad_note():
    from lib import proc_kill
    me = proc_kill.process_identity(os.getpid())
    assert proc_kill.stop_if_same(os.getpid(), me or {}) == "kept"
    assert proc_kill.stop_if_same("x", {}) == "kept"
    assert proc_kill.stop_if_same(0, {}) == "kept"
    assert proc_kill.stop_if_same(123, "not a dict") == "kept"



def _sleep_child(seconds=60, **kw):
    import subprocess
    return subprocess.Popen([sys.executable, "-c", f"import time; time.sleep({seconds})"], **kw)


@pytest.fixture
def child():
    proc = _sleep_child(start_new_session=True)
    yield proc
    if proc.poll() is None:
        proc.kill()
    proc.wait()


@linux_pidfd
def test_stop_if_same_treats_a_different_command_with_the_same_start_as_gone(child):
    from lib import proc_kill
    ident = dict(proc_kill.process_identity(child.pid), cmd="0" * 64)
    assert proc_kill.stop_if_same(child.pid, ident) == "gone"
    assert child.poll() is None


@linux_pidfd
def test_stop_if_same_keeps_a_matching_child_without_pidfd(child, monkeypatch):
    from lib import proc_kill
    ident = proc_kill.process_identity(child.pid)

    def no_pidfd(pid):
        raise OSError(38, "Function not implemented")
    monkeypatch.setattr(os, "pidfd_open", no_pidfd)
    assert proc_kill.stop_if_same(child.pid, ident) == "kept"
    assert child.poll() is None


@linux_pidfd
def test_stop_if_same_keeps_a_child_it_may_not_signal(child, monkeypatch):
    from lib import proc_kill
    ident = proc_kill.process_identity(child.pid)

    def denied(*a):
        raise PermissionError(1, "Operation not permitted")
    monkeypatch.setattr(os, "killpg", denied)
    monkeypatch.setattr(signal, "pidfd_send_signal", denied)
    assert proc_kill.stop_if_same(child.pid, ident) == "kept"
    assert child.poll() is None


@linux_pidfd
def test_stop_if_same_keeps_a_child_that_does_not_exit_in_time(child, monkeypatch):
    from lib import proc_kill
    ident = proc_kill.process_identity(child.pid)
    monkeypatch.setattr(os, "killpg", lambda *a: None)
    monkeypatch.setattr(signal, "pidfd_send_signal", lambda *a: None)
    assert proc_kill.stop_if_same(child.pid, ident, wait=0.2) == "kept"


@pytest.mark.parametrize("pid", [2**40, float("inf"), -1])
def test_stop_if_same_answers_kept_for_an_impossible_pid(pid):
    from lib import proc_kill
    assert proc_kill.stop_if_same(pid, {"start": "1", "cmd": "x"}) == "kept"


def test_stop_if_same_answers_kept_on_an_unexpected_error(monkeypatch):
    from lib import proc_kill
    monkeypatch.setattr(proc_kill, "_linux_stop_if_same", lambda *a: 1 / 0)
    monkeypatch.setattr(proc_kill, "_win_stop_if_same", lambda *a: 1 / 0)
    assert proc_kill.stop_if_same(12345, {"start": "1", "cmd": "x"}) == "kept"


@linux_pidfd
def test_stop_if_same_also_ends_a_wrappers_grandchild(tmp_path):
    import subprocess
    from lib import proc_kill
    marker = tmp_path / "grandchild.pid"
    script = ("import subprocess, sys, time\n"
              "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
              f"open({str(marker)!r}, 'w').write(str(g.pid))\n"
              "time.sleep(60)\n")
    leader = subprocess.Popen([sys.executable, "-c", script], start_new_session=True)
    try:
        end = time.monotonic() + 10
        while not (marker.exists() and marker.read_text()) and time.monotonic() < end:
            time.sleep(0.05)
        grandchild = int(marker.read_text())
        ident = proc_kill.process_identity(leader.pid)
        assert proc_kill.stop_if_same(leader.pid, ident) == "stopped"
        assert _wait_dead(grandchild)
    finally:
        if leader.poll() is None:
            leader.kill()
        leader.wait()
