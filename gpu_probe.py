"""gpu_probe.py -- nvidia-smi readings taken before background_jobs._lock.

Each nvidia-smi run can take up to 5 s. Read under that lock, it stalled every
get_status / update_progress / is_cancel_requested behind it. Callers call
prefetch() just before taking the lock; the slot decision inside the lock then
consumes that reading, taken moments earlier, instead of running the tool.
A reading is used once and only within MAX_AGE_SECONDS; anything else (cli.py,
which never prefetches, or a second read in one decision) reads live as before,
so the decision never rests on an old number.
"""

import threading
import time

MAX_AGE_SECONDS = 3.0

_local = threading.local()


def prefetch() -> None:
    """Never raises; a failed reading is simply not cached."""
    import diagnostics
    try:
        at = time.monotonic()
        load = diagnostics.external_gpu_load()
        _local.readings = {"busy": diagnostics.external_gpu_is_busy(load), "load": load, "at": at}
    except Exception:
        _local.readings = None


def _take(key):
    readings = getattr(_local, "readings", None)
    if readings is None or key not in readings or time.monotonic() - readings["at"] > MAX_AGE_SECONDS:
        return False, None
    return True, readings.pop(key)


def external_gpu_is_busy() -> bool:
    import diagnostics
    found, value = _take("busy")
    return value if found else diagnostics.external_gpu_is_busy()


def external_gpu_load():
    import diagnostics
    found, value = _take("load")
    return value if found else diagnostics.external_gpu_load()
