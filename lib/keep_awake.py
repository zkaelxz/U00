"""Keeps Windows from sleeping while there is work to finish, from one thread of its own."""
import os
import threading

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
CHECK_SECONDS = 5.0


def _windows_setter():
    if os.name != "nt":
        return None
    try:
        import ctypes
        fn = ctypes.windll.kernel32.SetThreadExecutionState
        fn.argtypes = [ctypes.c_uint]
        fn.restype = ctypes.c_uint
        return fn
    except (AttributeError, OSError):
        return None


class KeepAwake:
    """While `busy()` is true, asks Windows not to sleep from idleness (the
    display may still turn off). Windows ties the request to the thread that
    made it and drops it when that thread ends, so one long-lived thread both
    makes and clears it; each check sees the end of the last job within
    `interval` seconds. No-op off Windows unless a `set_state` is given."""

    def __init__(self, busy, set_state=None, interval: float | None = None):
        self._busy = busy
        self._set_state = set_state if set_state is not None else _windows_setter()
        self._interval = CHECK_SECONDS if interval is None else interval
        self._stop = threading.Event()
        self._thread = None
        self.holding = False

    def start(self) -> bool:
        if self._set_state is None or (self._thread is not None and self._thread.is_alive()):
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="keep-pc-awake")
        self._thread.start()
        return True

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    want = bool(self._busy())
                except Exception:
                    want = False   # a failed check must never keep the PC up for good
                if want != self.holding:
                    self._apply(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if want else 0))
                    self.holding = want
                self._stop.wait(self._interval)
        finally:
            if self.holding:
                self._apply(ES_CONTINUOUS)
                self.holding = False

    def _apply(self, flags: int) -> None:
        if self._set_state is None:
            return
        try:
            self._set_state(flags)
        except Exception:
            pass   # nothing to do about it; the next change tries again
