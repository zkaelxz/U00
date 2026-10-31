"""
lib/proc_kill's Windows decisions with kernel32 faked, so they run on any
OS: a leftover process is stopped only when its start time and image name
still match the note, every unclear answer keeps it, and the handle is
always closed.
"""
import ctypes

import pytest

from lib import proc_kill as pk

pytest.importorskip("ctypes.wintypes")

CREATED = (7 << 32) | 9
IMAGE = "C:\\Baihe\\python\\Python.EXE"
IDENTITY = {"start": str(CREATED), "cmd": pk._digest(b"python.exe")}


class Kernel32:
    def __init__(self, handle=42, exit_code=pk._WIN_STILL_ACTIVE, times_ok=True,
                 image=IMAGE, terminate_ok=True, wait_result=0):
        self.handle, self.exit_code, self.times_ok = handle, exit_code, times_ok
        self.image, self.terminate_ok, self.wait_result = image, terminate_ok, wait_result
        self.calls = []

    def OpenProcess(self, access, inherit, pid):
        self.calls.append(("open", access, pid))
        return self.handle

    def GetExitCodeProcess(self, handle, ref):
        ref._obj.value = self.exit_code
        return True

    def GetProcessTimes(self, handle, created, *_rest):
        if not self.times_ok:
            return False
        created._obj.dwHighDateTime, created._obj.dwLowDateTime = CREATED >> 32, CREATED & 0xFFFFFFFF
        return True

    def QueryFullProcessImageNameW(self, handle, flags, buf, size):
        if self.image is None:
            return False
        buf.value = self.image
        return True

    def TerminateProcess(self, handle, code):
        self.calls.append(("terminate", handle))
        return self.terminate_ok

    def WaitForSingleObject(self, handle, ms):
        self.calls.append(("wait", ms))
        return self.wait_result

    def CloseHandle(self, handle):
        self.calls.append(("close", handle))
        return True


@pytest.fixture
def kernel(monkeypatch):
    def install(**kw):
        k = Kernel32(**kw)
        monkeypatch.setattr(pk, "_win_kernel32", lambda: k)
        return k
    return install


def _closed(k):
    return k.calls[-1] == ("close", k.handle)


def test_identity_is_the_start_time_and_the_lowercased_image_name(kernel):
    k = kernel()
    assert pk._win_identity_from_handle(k, 42) == IDENTITY


@pytest.mark.parametrize("kw", [{"times_ok": False}, {"image": None}])
def test_no_identity_when_windows_cannot_say(kernel, kw):
    assert pk._win_identity_from_handle(kernel(**kw), 42) is None


def test_a_matching_process_is_terminated_and_waited_for(kernel):
    k = kernel()
    assert pk._win_stop_if_same(1234, IDENTITY, 2.0) == "stopped"
    assert ("terminate", 42) in k.calls and ("wait", 2000) in k.calls and _closed(k)
    access = k.calls[0][1]
    assert access & pk._WIN_TERMINATE and access & pk._WIN_SYNCHRONIZE


def test_a_process_that_has_not_exited_after_the_wait_is_kept(kernel):
    k = kernel(wait_result=0x102)                     # WAIT_TIMEOUT
    assert pk._win_stop_if_same(1234, IDENTITY, 0.5) == "kept"
    assert _closed(k)


def test_a_refused_terminate_is_kept(kernel):
    k = kernel(terminate_ok=False)
    assert pk._win_stop_if_same(1234, IDENTITY, 1) == "kept"
    assert not any(c[0] == "wait" for c in k.calls) and _closed(k)


@pytest.mark.parametrize("other", [
    {"start": "1", "cmd": IDENTITY["cmd"]},
    {"start": IDENTITY["start"], "cmd": pk._digest(b"ffmpeg.exe")},
])
def test_a_different_process_under_the_pid_is_gone_and_untouched(kernel, other):
    k = kernel()
    assert pk._win_stop_if_same(1234, other, 1) == "gone"
    assert not any(c[0] == "terminate" for c in k.calls) and _closed(k)


def test_an_exited_process_is_gone(kernel):
    k = kernel(exit_code=0)
    assert pk._win_stop_if_same(1234, IDENTITY, 1) == "gone"
    assert not any(c[0] == "terminate" for c in k.calls) and _closed(k)


def test_an_unreadable_identity_is_kept(kernel):
    k = kernel(times_ok=False)
    assert pk._win_stop_if_same(1234, IDENTITY, 1) == "kept"
    assert not any(c[0] == "terminate" for c in k.calls) and _closed(k)


@pytest.mark.parametrize("error, answer", [(87, "gone"), (5, "kept"), (0, "kept")])
def test_no_handle_is_gone_only_for_an_unknown_pid(kernel, monkeypatch, error, answer):
    k = kernel(handle=0)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: error, raising=False)
    assert pk._win_stop_if_same(1234, IDENTITY, 1) == answer
    assert not any(c[0] in ("terminate", "close") for c in k.calls)


def test_no_kernel32_is_kept(monkeypatch):
    def broken():
        raise OSError("no kernel32")
    monkeypatch.setattr(pk, "_win_kernel32", broken)
    assert pk._win_stop_if_same(1234, IDENTITY, 1) == "kept"
