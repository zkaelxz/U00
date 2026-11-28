"""
services/extension_service.py -- the browser-extension bridge's on/off
switch, token and translation settings, behind Settings > Browser extension.

The bridge is `services/page_server.py`: a loopback-only HTTP endpoint on a fixed
port that needs its own shared token on every request. It runs on its own
thread with no API request to read settings from, so this module is how
saved state reaches it: its on/off state is the Sources setting
`page_server_enabled` (sources/store.py, the one api/background.py reads at
startup), and `push_translation_config` registers a provider that
page_server calls on every request. Resolving per request means a key,
engine or OCR path saved in Settings applies to the next page without a
restart.

Every function here but `extension_zip` is meant for `local_only()` routes (the zip holds no token
and goes to any signed-in member who may use the extension), because the token
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

import io
import os
import threading
import zipfile

import db
from services import page_server
from engine_backends import engine_registry
from services import settings_service, translate_service
from lib import file_manager
from lib.errors import InvalidInputError, NotFoundError, UnsupportedOperationError
import portable
from sources import store as src_store

ENGINE_SETTING = "extension_translation_engine"

# Two toggles at once (a double click, two tabs) would otherwise interleave
# so that "off" saves and reports stopped while "on" opens the port after it.
_toggle_lock = threading.Lock()


_EXTENSION_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "extension")
# The end-to-end driver is a developer tool; it is not part of what a browser loads.
_NOT_SHIPPED = {"verify_end_to_end.py"}


def _shipped_files() -> list[tuple[str, str]]:
    """(absolute path, path relative to the extension folder) of every file a browser loads."""
    found = []
    for root, dirs, files in os.walk(_EXTENSION_DIR):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for name in sorted(files):
            full = os.path.join(root, name)
            # The zip reaches signed-in members, so a stray dotfile (.env, an editor swap file) or a
            # link to a file elsewhere in a checkout must never ride along.
            if name in _NOT_SHIPPED or name.endswith(".pyc") or name.startswith(".") or os.path.islink(full):
                continue
            found.append((full, os.path.relpath(full, _EXTENSION_DIR)))
    return found


def synced_folder() -> str:
    """The one folder the browser loads the extension from. It sits in the data folder, not
    the program folder, so its path survives app updates and moving the install."""
    return os.path.join(portable.data_dir(), "browser-extension")


def sync_extension_folder() -> bool:
    """Makes `synced_folder()` match the extension this app ships; True if anything changed.
    The browser never updates an extension loaded from a folder, but the extension reloads
    itself when the app reports a newer version (background.js), so keeping this folder
    current at every start is what turns an app update into an extension update. Files are
    swapped in one at a time with manifest.json last, so a reload never sees a new manifest
    beside old scripts."""
    if not os.path.isfile(os.path.join(_EXTENSION_DIR, "manifest.json")):
        return False
    target = synced_folder()
    wanted = _shipped_files()
    changed = False
    for full, rel in sorted(wanted, key=lambda pair: pair[1] == "manifest.json"):
        dest = os.path.join(target, rel)
        with open(full, "rb") as f:
            data = f.read()
        try:
            with open(dest, "rb") as f:
                if f.read() == data:
                    continue
        except OSError:
            pass
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp = dest + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, dest)
        changed = True
    keep = {os.path.normcase(os.path.join(target, rel)) for _, rel in wanted}
    for root, _dirs, files in os.walk(target):
        for name in files:
            leftover = os.path.join(root, name)
            if os.path.normcase(leftover) not in keep:
                os.remove(leftover)
                changed = True
    return changed


def open_synced_folder() -> dict:
    """Opens the synced extension folder in the PC's file manager. With no desktop (the
    background service), the folder rides in the answer for the PC owner to copy; the
    route is local_only, so the path never leaves the PC."""
    sync_extension_folder()
    folder = synced_folder()
    try:
        file_manager.open_folder(folder)
    except file_manager.NoDesktopError:
        return {"opened": False, "folder": folder}
    except OSError:
        raise UnsupportedOperationError("This PC has no file manager the app can open.") from None
    return {"opened": True}


def extension_zip() -> bytes:
    """The browser extension as a zip, so it can be unpacked on a computer that
    cannot open this PC's app folder (the folder is hard to find in a service install)."""
    if not os.path.isfile(os.path.join(_EXTENSION_DIR, "manifest.json")):
        raise NotFoundError("The extension files were not found in this install.")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for full, rel in _shipped_files():
            zf.write(full, os.path.join("baihe-extension", rel))
    return buf.getvalue()


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
            page_server.warm_models_soon()
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
    """(engine, model) from app settings. Never saved: Scanlate's default
    engine, so a first page is translated without a trip to Settings. Saved as
    "no engine" (OCR only), unknown to this build, or unreadable: (None, None).
    A local Ollama tag saved before those were removed reads as no model."""
    try:
        saved = db.get_app_setting(ENGINE_SETTING, None)
    except Exception:
        return None, None
    if saved is None:
        from services import scanlate_pages_service
        try:
            return scanlate_pages_service.default_engine(), None
        except Exception:
            return None, None
    if not isinstance(saved, dict):
        return None, None
    engine = saved.get("engine")
    if engine not in engine_registry.ENGINES:
        return None, None
    model = engine_registry.drop_saved_local_model(engine, saved.get("model"))
    return engine, (model if isinstance(model, str) and model else None)


def _engine_entry(engines: list, name):
    return next((e for e in engines if e["name"] == name), None)


def _ocr_config() -> dict:
    """The saved OCR settings the Scanlate run reads (services/scanlate_run_service.py).
    The backend stays None for "auto" because the bridge only learns the
    page's language per request, and scanlate picks the same
    language-based default from `prefer_paddle_vl_manga` there."""
    backend = settings_service.get("ocr_backend")
    return {
        "hf_token": settings_service.resolve_key("hf_token") or None,
        "tesseract_cmd": settings_service.get("tesseract_cmd") or None,
        "ocr_backend": None if backend in (None, "", "auto") else backend,
        "prefer_paddle_vl_manga": bool(settings_service.get("ocr_prefer_paddle_vl_manga")),
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
    return {
        "engine": engine,
        "model": model,
        "api_key": key or "",
        "free_tier": engine == "gemini" and settings_service.get("gemini_free_tier"),
        "base_url": settings_service.ollama_url() if engine == "ollama" else None,
        **ocr,
    }


def page_extras(drama, config: dict, engine, image: bytes | None) -> dict:
    """What a Scanlate run sends with a page besides its text, for the same
    quality: the series cast's pronouns and voice notes, and the page's
    picture (when the setting is on and a reader can take it: run.picture_readers).
    Keys are the `translate_page_bubbles` arguments."""
    from engine_backends import paid_calls
    from services import scanlate_run_service as run
    extras = {"character_notes": run.load_translation_guide(drama or {})[1]}
    name = config.get("engine") or ""
    readers = (run.picture_readers(engine, name)
               if image and settings_service.get("scanlate_page_image") else [])
    picture = run.page_picture(image, (drama or {}).get("source_language")) if image and readers else None
    if picture is not None:
        data, mime, scale = picture
        extras["page_image"] = {
            "data": data, "mime": mime, "scale": scale,
            "readers": [{"engine": reader, "usage_cb": paid_calls.usage_logger(
                (drama or {}).get("id"), reader_name, reader, "extension_translate"),
                **({"on_failure": run._note_ollama_reader_failed}
                   if getattr(reader, "model", None) == run.PAGE_IMAGE_OLLAMA_MODEL else {})}
                for reader, reader_name in readers]}
    return extras


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
        if not isinstance(engine, str) or engine not in engine_registry.ENGINES:
            raise InvalidInputError(engine_registry.unknown_engine_message(engine))
    if model is not None:
        entry = _engine_entry(translate_service.list_engines(), engine) if engine else None
        if not isinstance(model, str) or not entry or model not in (entry["models"] or ()):
            raise InvalidInputError("Unknown model for this engine.")
    if engine is None:
        # Kept as a saved choice, so "no engine" is not mistaken for "never chosen".
        db.set_app_setting(ENGINE_SETTING, {"engine": None, "model": None})
    else:
        db.set_app_setting(ENGINE_SETTING, {"engine": engine, "model": model})
    push_translation_config()
    return get_translation_settings()
