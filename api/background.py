"""
api/background.py -- the background pieces the Streamlit app used to start,
started once when the API process starts (FastAPI lifespan in
`api/server.py`), so they keep running after Streamlit is retired.

What Streamlit starts, and when (read from the tabs, since `app.py` itself
only renders them):
- the chapter-check scheduler (`sources.chapter_check.ensure_scheduler_started`):
  unconditionally, on every Sources tab render. It is a no-op loop unless
  `check_interval_hours` > 0 and a series is tracked.
- the browser-extension endpoint (`page_server.ensure_server_started`, loopback
  port 8756): only while the Sources setting `page_server_enabled` is on,
  from the Settings sidebar. The API starts it under the same setting.

Both `ensure_*` functions are once-per-process and safe to call again, so
this is idempotent. Off when `ApiSettings.background_services` is False:
the dataclass default (every test that builds `ApiSettings(...)`) and
`BAIHE_API_BACKGROUND=0` (tests/conftest.py sets it for `load_settings`).
Nothing is stopped at shutdown: both are daemon threads.

Not done here: the Settings sidebar also pushes the extension's engine
choice and key into `page_server.set_translation_config` from Streamlit
session state. That choice is not persisted anywhere, so under the API the
endpoint runs with no engine and returns OCR text marked untranslated until
a config route exists.
"""

import threading

_started = None

# GPU-queue nudge: background_jobs never re-checks its GPU queue on its own
# (a job queued behind GPU load Baihe didn't start is re-checked only when
# another GPU job finishes). The Streamlit Diagnostics tab's auto-refresh
# called background_jobs.recheck_gpu_queue on every tick; with the tab gone
# the API does it on a timer, only while background services are on, and
# stops it at shutdown. The call is a cheap no-op when nothing is queued.
GPU_QUEUE_POLL_SECONDS = 20.0
_gpu_poller = None          # (thread, stop_event) while running
_gpu_lock = threading.Lock()


def start_gpu_queue_poller(interval: float = None) -> bool:
    """Starts the periodic GPU-queue re-check; returns False if one is
    already running in this process."""
    global _gpu_poller
    interval = GPU_QUEUE_POLL_SECONDS if interval is None else float(interval)
    with _gpu_lock:
        if _gpu_poller is not None and _gpu_poller[0].is_alive():
            return False
        stop = threading.Event()

        def loop():
            while not stop.wait(interval):
                try:
                    import background_jobs
                    background_jobs.recheck_gpu_queue()
                except Exception as exc:
                    _log("GPU queue re-check failed: %s", exc)

        thread = threading.Thread(target=loop, daemon=True, name="api-gpu-queue-poller")
        _gpu_poller = (thread, stop)
        thread.start()
        return True


def stop_gpu_queue_poller(timeout: float = 5.0) -> None:
    global _gpu_poller
    with _gpu_lock:
        poller, _gpu_poller = _gpu_poller, None
    if poller is not None:
        poller[1].set()
        poller[0].join(timeout)


def start_background_services() -> dict:
    """Starts what is due; returns {"chapter_scheduler": bool,
    "page_server": bool} (True = running after this call). Never raises: a
    failure is logged and the API still starts."""
    global _started
    if _started is not None:
        return dict(_started)
    state = {"chapter_scheduler": False, "page_server": False}
    try:
        from sources import chapter_check
        chapter_check.ensure_scheduler_started()
        state["chapter_scheduler"] = True
    except Exception as exc:
        _log("chapter-check scheduler did not start: %s", exc)
    try:
        from sources import store as src_store
        if bool(src_store.get_setting("page_server_enabled")):
            import page_server
            page_server.ensure_server_started()
            state["page_server"] = page_server.server_running()
    except Exception as exc:
        _log("browser-extension endpoint did not start: %s", exc)
    _started = state
    return dict(state)


def _log(fmt, *args):
    try:
        from applog import get_logger
        from translate_engines import redact_secrets
        get_logger().warning(fmt, *(redact_secrets(str(a)) for a in args))
    except Exception:
        pass
