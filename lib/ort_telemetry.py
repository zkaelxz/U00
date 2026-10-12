"""Turns ONNX Runtime's usage reporting off before this process makes a session.

ONNX Runtime's official Windows builds send usage events by default; Baihe is
local-first and sends nothing, so every process that can create a session
(the server, a GPU worker, the CLI) calls this first. The switch is
per process, so a spawned worker has to call it too. Never raises: with no
onnxruntime installed, or a build without the switch, there is nothing to turn off.
"""

_done = False


def disable() -> bool:
    """True once the switch has been thrown in this process."""
    global _done
    if _done:
        return True
    try:
        import onnxruntime
        off = getattr(onnxruntime, "disable_telemetry_events", None)
        if off is None:
            return False
        off()
    except Exception:   # noqa: BLE001 -- a privacy nicety must never fail a job
        return False
    _done = True
    return True
