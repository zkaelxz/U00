"""lib/pid.py: the one "is this process still running" probe."""
import os
import subprocess
import sys

import pytest

import background_jobs
from lib import pid as pid_lib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "installer"))

import launcher  # noqa: E402


class _K32:
    def __init__(self, handle=1, exit_code=259, ok=True):
        self.handle, self.exit_code, self.ok = handle, exit_code, ok
        self.closed = []

    def OpenProcess(self, access, inherit, pid):
        return self.handle

    def GetExitCodeProcess(self, handle, ref):
        ref._obj.value = self.exit_code
        return self.ok

    def CloseHandle(self, handle):
        self.closed.append(handle)


@pytest.mark.parametrize("k32, last_error, alive", [
    (_K32(exit_code=259), 0, True),
    (_K32(exit_code=0), 0, False),
    (_K32(ok=False), 0, True),            # the exit-code query failed: unknown, never dead
    (_K32(handle=0), 5, True),            # access denied: it exists
    (_K32(handle=0), 87, False),          # no such process
])
def test_windows_liveness(k32, last_error, alive):
    assert pid_lib._windows_pid_alive(5, k32, lambda: last_error) is alive


def test_the_windows_handle_is_closed_whatever_the_answer():
    for code in (259, 0):
        k32 = _K32(exit_code=code)
        pid_lib._windows_pid_alive(5, k32, lambda: 0)
        assert k32.closed == [1]


def test_our_own_process_is_alive():
    assert pid_lib.pid_alive(os.getpid())


@pytest.mark.skipif(os.name == "nt", reason="the Windows branch is covered through _windows_pid_alive")
def test_an_exited_child_is_dead():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    assert not pid_lib.pid_alive(proc.pid)


@pytest.mark.parametrize("bad", [0, -1, 0x1_0000_0000])
def test_a_pid_no_os_accepts_is_dead(bad):
    assert not pid_lib.pid_alive(bad)


@pytest.mark.skipif(os.name == "nt", reason="os.kill(pid, 0) is not a probe on Windows")
@pytest.mark.parametrize("error, alive", [
    (ProcessLookupError(), False),
    (PermissionError(), True),            # exists, owned by someone else
    (OverflowError(), False),             # a corrupted lock file can hold a pid os.kill refuses
    (OSError("odd"), True),               # unknown: erring towards dead would run a second installer
])
def test_posix_probe_outcomes(monkeypatch, error, alive):
    def kill(pid, sig):
        raise error
    monkeypatch.setattr(pid_lib.os, "kill", kill)
    assert pid_lib.pid_alive(4242) is alive


class TestOwnerProcessAlive:
    """background_jobs.owner_process_alive keeps its own coercion: a pid it cannot read is alive."""

    @pytest.mark.parametrize("value", [None, "x", "", 0, -3, "0", 2 ** 40])
    def test_an_unreadable_or_non_positive_pid_reads_alive(self, value):
        assert background_jobs.owner_process_alive(value) is True

    @pytest.mark.skipif(os.name == "nt", reason="a DWORD pid is real on Windows")
    def test_a_pid_past_posix_pid_t_reads_alive(self):
        assert background_jobs.owner_process_alive(2 ** 31 + 5) is True

    def test_a_numeric_string_is_probed(self, monkeypatch):
        seen = []
        monkeypatch.setattr(pid_lib, "pid_alive", lambda pid: seen.append(pid) or False)
        assert background_jobs.owner_process_alive("4242") is False
        assert seen == [4242]

    def test_a_probe_that_raises_reads_alive(self, monkeypatch):
        def boom(pid):
            raise RuntimeError("kernel32 missing")
        monkeypatch.setattr(pid_lib, "pid_alive", boom)
        assert background_jobs.owner_process_alive(4242) is True


def test_the_launcher_shares_the_probe():
    assert launcher._pid_alive is pid_lib.pid_alive



def test_our_own_start_stamp_is_the_same_each_time():
    if os.name != "nt" and not os.path.isdir("/proc/self"):
        pytest.skip("no start-stamp probe on this OS")
    stamp = pid_lib.process_start_token(os.getpid())
    assert stamp and stamp == pid_lib.process_start_token(os.getpid())


@pytest.mark.skipif(not os.path.isdir("/proc/self"), reason="Linux /proc probe")
def test_an_exited_child_has_no_start_stamp():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    assert pid_lib.process_start_token(proc.pid) is None


def test_linux_stamp_counts_fields_from_the_last_parenthesis(tmp_path):
    (tmp_path / "sys" / "kernel" / "random").mkdir(parents=True)
    (tmp_path / "sys" / "kernel" / "random" / "boot_id").write_text("b00t\n")
    (tmp_path / "42").mkdir()
    # A command name may hold ") " itself; starttime is field 22.
    fields = ["S"] + [str(n) for n in range(4, 22)] + ["777", "0"]
    (tmp_path / "42" / "stat").write_text("42 (odd) name) " + " ".join(fields) + "\n")
    assert pid_lib._linux_start_token(42, str(tmp_path)) == "lb00t-777"
    assert pid_lib._linux_start_token(43, str(tmp_path)) is None


class _TimesK32(_K32):
    def __init__(self, ticks, ok=True, handle=1):
        super().__init__(handle=handle)
        self.ticks, self.times_ok = ticks, ok

    def GetProcessTimes(self, handle, created, *_rest):
        created._obj.dwHighDateTime = self.ticks >> 32
        created._obj.dwLowDateTime = self.ticks & 0xFFFFFFFF
        return self.times_ok


def test_windows_stamp_is_the_creation_filetime():
    k32 = _TimesK32(0x01DB_1234_5678_9ABC)
    assert pid_lib._windows_start_token(5, k32) == f"w{0x01DB_1234_5678_9ABC}"
    assert k32.closed == [1]


@pytest.mark.parametrize("k32", [_TimesK32(10**17, ok=False), _TimesK32(10**17, handle=0),
                                 _TimesK32(0)])
def test_windows_stamp_unknown_is_none(k32):
    assert pid_lib._windows_start_token(5, k32) is None
