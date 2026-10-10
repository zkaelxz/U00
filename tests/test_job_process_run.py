"""job_process_run: the one runner for long external commands. Real child
processes (python -c), no network; the POSIX cases need process groups."""
import os
import signal
import sys
import threading
import time

import pytest

import job_process_run

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
    items = list(job_process_run.stream_tree([sys.executable, "-c", script], 60.0,
                                             drain_seconds=2.0, cancel=flag.is_set))
    assert time.monotonic() - t0 < 15
    assert items[-1]["cancelled"] is True and items[-1]["timed_out"] is False
    assert _wait_dead(int(marker.read_text())), "grandchild survived the cancel"


@posix
def test_timeout_kills_the_whole_tree(tmp_path):
    marker = tmp_path / "gc.pid"
    items = list(job_process_run.stream_tree([sys.executable, "-c", _tree_script(marker)], 1.0,
                                             drain_seconds=2.0))
    assert items[0] == {"line": "started"}
    assert items[-1]["timed_out"] is True and items[-1]["cancelled"] is False
    assert _wait_dead(int(marker.read_text()))


@posix
def test_a_grandchild_holding_the_pipe_cannot_hang_stream_tree(tmp_path):
    marker = tmp_path / "gc.pid"
    try:
        t0 = time.monotonic()
        items = list(job_process_run.stream_tree(
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
        out = job_process_run.run_captured(
            [sys.executable, "-c", _tree_script(marker, same_group=False, parent_sleeps=60)], 1.0,
            drain_seconds=1.0)
        assert time.monotonic() - t0 < 15
        assert out.timed_out is True and "started" in out.stdout
    finally:
        _kill_marker(marker)


@posix
def test_closing_the_generator_early_kills_the_child(tmp_path):
    marker = tmp_path / "gc.pid"
    gen = job_process_run.stream_tree([sys.executable, "-c", _tree_script(marker)], 60.0,
                                      drain_seconds=2.0)
    assert next(gen) == {"line": "started"}
    gen.close()
    assert _wait_dead(int(marker.read_text()))


def test_run_captured_keeps_stdout_and_stderr_apart():
    script = ("import sys\nprint('out')\nprint('err', file=sys.stderr)\nsys.exit(3)\n")
    out = job_process_run.run_captured([sys.executable, "-c", script], 30.0)
    assert (out.returncode, out.stdout.strip(), out.stderr.strip()) == (3, "out", "err")
    assert out.timed_out is False and out.cancelled is False


def test_run_captured_gives_the_child_no_stdin():
    out = job_process_run.run_captured(
        [sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"], 30.0)
    assert out.stdout.strip() == "''"


def test_run_captured_reports_a_missing_program_as_oserror():
    with pytest.raises(OSError):
        job_process_run.run_captured(["definitely-not-a-program-baihe"], 5.0)


def test_run_captured_keeps_only_the_newest_output(monkeypatch):
    monkeypatch.setattr(job_process_run, "_CAPTURE_LIMIT_CHARS", 100)
    script = "for i in range(500):\n    print('line', i)\n"
    out = job_process_run.run_captured([sys.executable, "-c", script], 30.0)
    assert "line 499" in out.stdout and "line 0\n" not in out.stdout
    assert len(out.stdout) < 400


@pytest.mark.skipif(os.name == "nt", reason="uses a shebang script as the fake venv python")
def test_throwaway_venv_probe_survives_a_non_utf8_child_locale(tmp_path, monkeypatch):
    import diagnostics

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
    monkeypatch.setattr(diagnostics, "_venv_python", lambda venv_dir: str(fake))

    venv_py, err = diagnostics._make_throwaway_venv(
        str(tmp_path), "trial", sys.executable, ["/parent/site"])

    assert err is None
    assert (wanted / "_baihe_parent_env.pth").read_text(encoding="utf-8") == "/parent/site\n"
