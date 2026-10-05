"""
services/extension_service.py -- the browser-extension bridge's on/off
switch, token and translation settings, behind Settings > Browser extension.

The bridge is `page_server.py`: a loopback-only HTTP endpoint on a fixed
port that needs its own shared token on every request. It runs on its own
thread with no API request to read settings from, so this module is how
saved state reaches it: its on/off state is the Sources setting
`page_server_enabled` (sources/store.py, the one api/background.py reads at
startup), and `push_translation_config` registers a provider that
page_server calls on every request. Resolving per request means a key,
engine or OCR path saved in Settings applies to the next page without a
restart.

Every function here is meant for `local_only()` routes, because the token
and the settings behind them must not be reachable by a remote household
user. `get_status` never returns the port or the token; only `reveal_token`
returns the token. Keys and the Hugging Face token never leave the PC: the
config this module builds is for page_server only, and the API reports
whether a key is configured, never its value.

The OCR settings (Tesseract path, OCR backend, PaddleOCR-VL preference, HF
token) come from the same settings the Comic Scanlate run reads, so a page
read through the extension is OCR'd like the same page in the app.

Turning the bridge off persists the setting and stops the endpoint in this
process at once (`page_server.stop_server`, safe from an API request
thread), so `restart_needed` stays False unless it could not be stopped.
"""

import threading

import db
import page_server
import translate_engines
from services import settings_service, translate_service
from services.service_errors import InvalidInputError
from sources import store as src_store

ENGINE_SETTING = "extension_translation_engine"

# Two toggles at once (a double click, two tabs) would otherwise interleave
# so that "off" saves and reports stopped while "on" opens the port after it.
_toggle_lock = threading.Lock()


def get_status() -> dict:
    """{enabled, running}: the stored setting and whether this process
    serves the endpoint. No port, no token."""
    return {"enabled": bool(src_store.get_setting("page_server_enabled")),
            "running": bool(page_server.server_running())}


def set_enabled(enabled, start_now: bool = True) -> dict:
    """Persists `page_server_enabled`. When turning it on and `start_now`
    (the API passes its own background-services flag, so a process that
    starts no background pieces never opens the port) the endpoint is
    started in this process, as the startup hook would. Turning it off
    stops the endpoint whatever `start_now` is. Returns
    {enabled, running, restart_needed}."""
    if not isinstance(enabled, bool):
        raise InvalidInputError("enabled must be true or false.")
    with _toggle_lock:
        src_store.set_setting("page_server_enabled", enabled)
        if enabled and start_now:
            push_translation_config()
            page_server.ensure_server_started()
        elif not enabled:
            page_server.stop_server()
        status = get_status()
    status["restart_needed"] = bool(status["running"] and not enabled)
    return status


def reveal_token(confirm=False) -> dict:
    """{token}: the extension's shared token (created on first use). Needs
    confirm=True. The caller must not log or cache the response."""
    if confirm is not True:
        raise InvalidInputError("Showing the extension token needs confirm=true.")
    return {"token": page_server.load_or_create_token()}


def _saved_engine() -> tuple:
    """(engine, model) from app settings; (None, None) when unset, unknown
    to this build, or unreadable."""
    try:
        saved = db.get_app_setting(ENGINE_SETTING, None)
    except Exception:
        return None, None
    if not isinstance(saved, dict):
        return None, None
    engine = saved.get("engine")
    if engine not in translate_engines.ENGINES:
        return None, None
    model = saved.get("model")
    return engine, (model if isinstance(model, str) and model else None)


def _engine_entry(engines: list, name):
    return next((e for e in engines if e["name"] == name), None)


def _ocr_config() -> dict:
    """The saved OCR settings the Scanlate run reads (services/scanlate_run_service.py).
    The backend stays None for "auto" because the bridge only learns the
    page's language per request, and scanlate picks the same
    language-based default from `prefer_paddle_vl_manga` there."""
    backend = settings_service.get_preference("ocr_backend")
    return {
        "hf_token": settings_service.resolve_key("hf_token") or None,
        "tesseract_cmd": settings_service.get_tesseract_cmd(),
        "ocr_backend": None if backend in (None, "", "auto") else backend,
        "prefer_paddle_vl_manga": bool(settings_service.get_preference("ocr_prefer_paddle_vl_manga")),
    }


def resolved_translation_config() -> dict:
    """Server-side only: the page_server config for the saved engine,
    including its key. Never return this over HTTP."""
    engine, model = _saved_engine()
    ocr = _ocr_config()
    if engine is None:
        return {"engine": None, "model": None, "api_key": "", "free_tier": False,
                "base_url": None, **ocr}
    key = translate_service.resolve_api_key(engine)
    if key is None and engine == "nllb":
        key = "local"       # NLLB needs no key; page_server only wants a non-empty one
    return {
        "engine": engine,
        "model": model,
        "api_key": key or "",
        "free_tier": engine == "gemini" and settings_service.get_gemini_free_tier(),
        "base_url": (settings_service.resolve_key("ollama_url") or None) if engine == "ollama" else None,
        **ocr,
    }


def push_translation_config() -> None:
    """Registers the per-request provider with page_server and pushes the
    current values once (idempotent)."""
    page_server.set_config_provider(resolved_translation_config)
    page_server.set_translation_config(**resolved_translation_config())


def get_translation_settings() -> dict:
    """{engine, model, ready, engines}: the saved choice, whether it can
    translate now (engine set and its key configured), and the picker's
    options (translate_service.list_engines: key presence only)."""
    engines = translate_service.list_engines()
    engine, model = _saved_engine()
    entry = _engine_entry(engines, engine)
    return {"engine": engine, "model": model,
            "ready": bool(entry and entry["key_configured"]),
            "engines": engines}


def set_translation_settings(engine, model=None) -> dict:
    """Saves the extension's engine (None = translate nothing, OCR only)
    and optional model key, then pushes it to page_server. Messages never
    echo the input."""
    if engine is not None:
        if not isinstance(engine, str) or engine not in translate_engines.ENGINES:
            raise InvalidInputError(translate_engines.unknown_engine_message(engine))
    if model is not None:
        entry = _engine_entry(translate_service.list_engines(), engine) if engine else None
        if not isinstance(model, str) or not entry or model not in (entry["models"] or ()):
            raise InvalidInputError("Unknown model for this engine.")
    if engine is None:
        db.set_app_setting(ENGINE_SETTING, None)
    else:
        db.set_app_setting(ENGINE_SETTING, {"engine": engine, "model": model})
    push_translation_config()
    return get_translation_settings()
