"""gpu_probe.py -- nvidia-smi readings taken before job_table._lock.

Each nvidia-smi run can take up to 5 s. Read under that lock, it stalled every
get_status / update_progress / is_cancel_requested behind it. Callers call
prefetch() just before taking the lock; the slot decision inside the lock then
consumes that reading, taken moments earlier, instead of running the tool.
A reading is used once and only within MAX_AGE_SECONDS; anything else (a caller
that never prefetches, or a second read in one decision) reads live as before,
so the decision never rests on an old number.
"""

import threading
import time
from typing import Any

MAX_AGE_SECONDS = 3.0

_local = threading.local()


def prefetch(held_mb: float = 0.0) -> None:
    """Never raises; a failed reading is simply not cached. held_mb: memory a
    job about to start will take over rather than add to (an idle warm
    worker's), read as free."""
    import diagnostics_torch
    try:
        load = diagnostics_torch.external_gpu_load()
        if load and held_mb > 0:
            held = min(held_mb, load["memory_used_mb"])
            load = {**load, "memory_used_mb": load["memory_used_mb"] - held,
                    "memory_free_mb": load["memory_free_mb"] + held}
        # Stamped after the probe: a nvidia-smi that hangs to its timeout
        # returns None, and that "unknown" must still count as fresh or the
        # locked path would re-run the slow probe under the lock.
        at = time.monotonic()
        _local.readings = {"busy": diagnostics_torch.external_gpu_is_busy(load), "load": load, "at": at}
    except Exception:
        _local.readings = None


def _take(key) -> tuple[bool, Any]:
    readings = getattr(_local, "readings", None)
    if readings is None or key not in readings or time.monotonic() - readings["at"] > MAX_AGE_SECONDS:
        return False, None
    return True, readings.pop(key)


def prefetched_load():
    """The prefetched card reading, left in place; None when there is no fresh one."""
    readings = getattr(_local, "readings", None)
    if not readings or time.monotonic() - readings["at"] > MAX_AGE_SECONDS:
        return None
    return readings.get("load")


def prefetched_busy() -> bool:
    """The prefetched busy reading, left in place for the decision it was
    taken for; False when there is no fresh one."""
    readings = getattr(_local, "readings", None)
    return bool(readings and readings.get("busy") and time.monotonic() - readings["at"] <= MAX_AGE_SECONDS)


def external_gpu_is_busy() -> bool:
    import diagnostics_torch
    found, value = _take("busy")
    return value if found else diagnostics_torch.external_gpu_is_busy()


def external_gpu_load():
    import diagnostics_torch
    found, value = _take("load")
    return value if found else diagnostics_torch.external_gpu_load()
