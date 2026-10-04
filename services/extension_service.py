"""
services/extension_service.py -- the browser-extension bridge's on/off
switch and token, for the PC-only React control that replaces the Settings
sidebar's "Browser extension" expander (tabs/settings_tab.py:430-484) once
Streamlit is gone. User decision, 2026-09-29.

The bridge is `page_server.py`: a loopback-only HTTP endpoint (port 8756)
that needs its own shared token on every request. Its on/off state is the
Sources setting `page_server_enabled` (sources/store.py), the same one the
API startup hook (api/background.py) reads.

Every function here is meant for `local_only()` routes. `get_status` never
returns the port or the token; only `reveal_token` returns the token.

Translation engine (inventory row G16): Streamlit pushed the extension's
engine and key into `page_server` from session state, so nothing was saved
and the API ran the bridge untranslated. The engine and model are now an
app setting (`extension_translation_engine`); `push_translation_config`
registers a provider with `page_server` that resolves them, and the key from
.env, on every request. Keys never leave the PC: no function here returns a
key, only whether one is configured.

Stopping: turning the bridge off persists the setting and stops the
endpoint in this process at once (`page_server.stop_server`, safe from an
API request thread), so `restart_needed` stays False unless it could not
be stopped.
"""

import db
import page_server
import translate_engines
from services import settings_service, translate_service
from services.service_errors import InvalidInputError
from sources import store as src_store

ENGINE_SETTING = "extension_translation_engine"


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


def resolved_translation_config() -> dict:
    """Server-side only: the page_server config for the saved engine,
    including its key. Never return this over HTTP."""
    engine, model = _saved_engine()
    if engine is None:
        return {"engine": None, "model": None, "api_key": "", "free_tier": False,
                "base_url": None}
    key = translate_service.resolve_api_key(engine)
    if key is None and engine == "nllb":
        key = "local"       # NLLB needs no key; page_server only wants a non-empty one
    return {
        "engine": engine,
        "model": model,
        "api_key": key or "",
        "free_tier": engine == "gemini" and settings_service.get_gemini_free_tier(),
        "base_url": (settings_service.resolve_key("ollama_url") or None) if engine == "ollama" else None,
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
