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

_started = None


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
