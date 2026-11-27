"""lib/device_use.py -- counts which device (GPU or CPU) each model step ran on, for one log line per page."""

import threading

_lock = threading.Lock()
_counts: dict = {}


def note(step: str, device: str) -> None:
    with _lock:
        _counts[(step, device)] = _counts.get((step, device), 0) + 1


def take() -> str:
    """"step GPU x3, step CPU x1" for everything noted since the last call (empty when nothing was), then forgets it."""
    with _lock:
        counts = dict(_counts)
        _counts.clear()
    return ", ".join(f"{step} {device} x{n}" for (step, device), n in sorted(counts.items()))
