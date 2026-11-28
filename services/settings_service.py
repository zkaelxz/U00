"""
services/settings_service.py -- settings resolution for the settings
endpoint (api/routers/settings_routes.py) and every service that needs a
key or preference, so all of them read the same env-var mapping.

API keys are server-side only.
resolve_key() is for server-side use (e.g. the translate route) --
never return its result over an HTTP response. key_status() and
get_settings_overview() are what an API route may expose: booleans only.
"""
from lib import clock
import os
import sqlite3
import threading
import time
from typing import Optional

import env_reader
from jobs import job_settings
import memory_headroom
import stored_settings
from lib import desktop_session, settings_schema
from lib.errors import InvalidInputError

# Per settings key, the env var name(s) to read, in priority order -- the
# first entry is also the canonical name set_engine_key()/set_endpoint_url()
# write back, so a saved key round-trips through the exact same name it
# would be read back under.
ENV_NAMES = {
    "claude": ("BAIHE_CLAUDE_KEY", "ANTHROPIC_API_KEY"),
    "deepseek": ("BAIHE_DEEPSEEK_KEY", "DEEPSEEK_API_KEY"),
    # Deliberately NOT falling back to GOOGLE_API_KEY here -- a key under
    # that name isn't guaranteed to also work as a Gemini API key
    # (different products, often different projects).
    "gemini": ("BAIHE_GEMINI_KEY", "GEMINI_API_KEY"),
    "openai": ("BAIHE_OPENAI_KEY", "OPENAI_API_KEY"),
    "groq": ("BAIHE_GROQ_KEY", "GROQ_API_KEY"),
    "hf_token": ("BAIHE_HF_TOKEN", "HF_TOKEN", "HUGGINGFACE_TOKEN"),
    "ollama_url": ("BAIHE_OLLAMA_URL",),
    "monthly_cap_usd": ("BAIHE_MONTHLY_CAP_USD",),
}

# The subset of ENV_NAMES that are actual keys/endpoints (not a numeric
# setting like monthly_cap_usd) -- what an API overview reports presence
# for.
_ENGINE_KEY_NAMES = tuple(k for k in ENV_NAMES if k != "monthly_cap_usd")


def resolve_key(settings_key: str, env_path: str | None = None) -> Optional[str]:
    """Server-side only: resolves settings_key's value from .env, then
    real environment variables, in ENV_NAMES' priority order. Never expose
    the return value over an API response -- callers that report status
    over HTTP must use key_status()/get_settings_overview() instead.
    """
    return env_reader.resolve_env_names(ENV_NAMES.get(settings_key, ()), env_path)


def ollama_url() -> str:
    """Server-side only, like resolve_key: the configured Ollama base URL,
    repaired (scheme added, trailing slash and /api dropped), or Ollama's
    default address when none is set."""
    return settings_schema.normalize_ollama_url(resolve_key("ollama_url"))


def ollama_endpoint_is_loopback() -> bool:
    """Whether the configured Ollama URL points at this PC."""
    import ipaddress
    from urllib.parse import urlsplit
    try:
        host = urlsplit(ollama_url()).hostname or ""
    except ValueError:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


# Default API port (api.api_config.DEFAULT_PORT) and the extension bridge's
# 8756 (page_server.DEFAULT_PORT). Services may not import api/, so the
# numbers are repeated here.
_BAIHE_FIXED_PORTS = (8600, 8756)
API_PORT_ENV = "BAIHE_API_PORT"
HOUSEHOLD_PORT_ENV = "BAIHE_API_HOUSEHOLD_PORT"


def baihe_own_ports() -> set:
    """Ports on this PC that are Baihe's own, which outbound features (ntfy,
    SearXNG, Jellyfin) must not be pointed at: the fixed ones above plus the
    configured BAIHE_API_PORT and BAIHE_API_HOUSEHOLD_PORT. Both the real
    environment and .env are read, so a port set in either is protected."""
    ports: set[int] = set(_BAIHE_FIXED_PORTS)
    env = env_reader.read_env_file()
    for name in (API_PORT_ENV, HOUSEHOLD_PORT_ENV):
        for raw in (os.environ.get(name), env.get(name)):
            try:
                ports.add(int(str(raw).strip()))
            except (TypeError, ValueError):
                pass
    return ports


def baihe_ports(api_port: int, household_port: int = 0, public_url: str = "") -> dict:
    """{"ports": [{key, label, port, active, how_to_change}]} for the
    Diagnostics Ports panel. The caller passes the API settings' values
    (services may not import api/). `port` is None for the household
    listener while it is off. Numbers and fixed text only."""
    from services import page_server
    from services import remote_health_service
    try:
        household = int(household_port or 0)
    except (TypeError, ValueError):
        household = 0
    try:
        bridge_running = bool(page_server.server_running())
    except Exception:
        bridge_running = False
    https_on = remote_health_service.remote_access_enabled(public_url, household)
    ports = [
        {"key": "api", "label": "Baihe (this PC's window)", "port": int(api_port), "active": True,
         "how_to_change": "Service install: Start menu > Baihe Studio service > Change port "
                          "(asks for administrator permission); changing or deleting "
                          "BAIHE_API_PORT does not move an installed service. Launcher "
                          "install: set BAIHE_API_PORT and restart Baihe."},
        {"key": "household", "label": "Household listener", "port": household or None,
         "active": household > 0,
         "how_to_change": "Service install: Start menu > Baihe Studio service > Turn remote access "
                          "on (it asks for the household port) or off. Otherwise set "
                          "BAIHE_API_HOUSEHOLD_PORT and restart Baihe; unset it to turn the "
                          "listener off."},
        {"key": "extension", "label": "Browser extension bridge", "port": page_server.DEFAULT_PORT,
         "active": bridge_running,
         "how_to_change": "Fixed: this port cannot be changed. Turn the bridge on or off in Settings."},
        {"key": "https", "label": "HTTPS for remote access (Caddy)", "port": 443, "active": https_on,
         "how_to_change": "Fixed: remote access always uses 443. It is only used once remote "
                          "access is set up."},
    ]
    return {"ports": ports}


def key_status(env_path: str | None = None) -> dict:
    """{settings_key: bool} for every engine key/endpoint setting --
    whether a value is configured, never the value itself (D2)."""
    return {key: bool(resolve_key(key, env_path)) for key in _ENGINE_KEY_NAMES}


def resolve_gemini_free_tier(value) -> bool:
    """A request's gemini_free_tier: None (omitted) means the persisted
    setting. A request may turn the free tier off for one run, but never on:
    the free tier lifts the spending cap and logs $0, so only the saved
    setting (what the owner told the server about the key) can grant it."""
    saved = bool(get("gemini_free_tier"))
    return saved if value is None else bool(value) and saved


def get_settings_overview(env_path: str | None = None) -> dict:
    """Non-secret settings snapshot for the FastAPI settings endpoint.
    Preferences hold paths (never a file's contents); endpoints hold only
    URLs that pass validate_endpoint_url (no userinfo, query or fragment),
    so neither can carry a secret."""
    from services import media_upload_service  # imports this module at load
    return {
        "engine_keys": key_status(env_path),
        "gpu_limit_enabled": job_settings.get_gpu_limit_enabled(),
        "gpu_max_parallel": job_settings.get_gpu_max_parallel(),
        "notify_on_completion": job_settings.get_notify_on_completion(),
        "desktop_popups": desktop_session.has_desktop(),
        "use_gpu": get("use_gpu"),
        "gemini_free_tier": get("gemini_free_tier"),
        "bulk_auto_resume": get("bulk_auto_resume"),
        "offer_provider_models": get("offer_provider_models"),
        "remote_uploads": remote_uploads_on(),
        "remote_uploads_since": _remote_uploads_since() if remote_uploads_on() else None,
        "remote_uploads_auto_off": remote_uploads_auto_off() or _auto_off_never_chosen(),
        "preferences": get_preferences(),
        "endpoints": endpoint_values(env_path),
        "upload_max_mb_from_env": media_upload_service.upload_limit_from_env(),
        "effective_upload_max_mb": media_upload_service.max_upload_bytes() // (1024 * 1024),
        "monthly_cap_env_usd": _parse_cap(resolve_key("monthly_cap_usd", env_path)),
        "effective_monthly_cap_usd": get_monthly_cap_usd(env_path),
        **month_spend_status(),
        "choices": preference_choices(),
    }


# Writable, non-secret switches whose value lives in app_settings under the
# schema's store_key. Keys are never here (D2); preferences are the schema's
# pref.* rows, endpoint URLs go through set_endpoint_url.
_DB_TOGGLES = ("use_gpu", "gemini_free_tier", "bulk_auto_resume", "offer_provider_models")

# With the opt-in auto-off, a stolen admin cookie's window to fill the disk with uploads
# ends this long after the switch (or the auto-off) was turned on.
REMOTE_UPLOADS_DAYS = 7


def _remote_uploads_since() -> float | None:
    on_at = get("remote_uploads.since")
    return float(on_at) if isinstance(on_at, (int, float)) and on_at > 0 else None


def remote_uploads_auto_off() -> bool:
    return bool(get("remote_uploads_auto_off"))


def _auto_off_never_chosen() -> bool:
    import db
    return not isinstance(db.get_app_setting("remote_uploads_auto_off"), bool)


def remote_uploads_on(now: float | None = None) -> bool:
    """Whether a signed-in admin away from the PC may upload (media.upload). Needs the switch
    and a valid turned-on stamp (a stamp from before the stamp was kept apart must not count);
    with the opt-in auto-off, also within REMOTE_UPLOADS_DAYS of the later of that stamp and
    the day the auto-off was chosen."""
    if not get("remote_uploads"):
        return False
    now = time.time() if now is None else now
    on_at = _remote_uploads_since()
    if on_at is None or not on_at <= now:
        return False
    # Never chosen: a switch from before this setting existed keeps its old 7 day lapse, so
    # one that had already lapsed does not come back on with the update. Turning the switch
    # on again records the choice (_set_remote_uploads). An unreadable row counts as never chosen.
    if _auto_off_never_chosen():
        return now < on_at + REMOTE_UPLOADS_DAYS * 86400
    if not remote_uploads_auto_off():
        return True
    start = max(on_at, float(get("remote_uploads.auto_off_from") or 0))
    return now < start + REMOTE_UPLOADS_DAYS * 86400


def _remote_uploads_audit(action: str, detail: str) -> None:
    from services import auth_service
    # Only the PC can flip this, so there is no acting account to name. The change is
    # already saved: a failing log write must not turn it into a 500.
    try:
        auth_service.write_audit(None, action, detail)
    except Exception:
        import applog
        applog.get_logger().warning("could not write the remote-uploads audit row", exc_info=True)


def _set_remote_uploads(enabled: bool) -> None:
    import db
    # Time first: a crash between the two writes leaves the switch off.
    if enabled:
        db.set_app_setting("remote_uploads.since", time.time())
        if _auto_off_never_chosen():
            db.set_app_setting("remote_uploads_auto_off", False)
    db.set_app_setting("remote_uploads", enabled)
    _remote_uploads_audit("remote_uploads.on" if enabled else "remote_uploads.off",
                          f"auto-off after {REMOTE_UPLOADS_DAYS} days" if enabled and remote_uploads_auto_off()
                          else "stays on until turned off" if enabled else "")


def _set_remote_uploads_auto_off(enabled: bool) -> None:
    import db
    # A lapsed switch still reads as stored-on; choosing either mode must not bring it back.
    if get("remote_uploads") and not remote_uploads_on():
        db.set_app_setting("remote_uploads", False)
        _remote_uploads_audit("remote_uploads.off", "lapsed")
    if enabled:
        db.set_app_setting("remote_uploads.auto_off_from", time.time())
    db.set_app_setting("remote_uploads_auto_off", enabled)
    _remote_uploads_audit("remote_uploads.auto_off" if enabled else "remote_uploads.stay_on",
                          f"{REMOTE_UPLOADS_DAYS} days" if enabled else "")


# Switches whose owner (a root module) keeps its own storage and setter.
_WRITABLE_SETTINGS = {
    "remote_uploads": _set_remote_uploads,
    "remote_uploads_auto_off": _set_remote_uploads_auto_off,
    "gpu_limit_enabled": job_settings.set_gpu_limit_enabled,
    "notify_on_completion": job_settings.set_notify_on_completion,
}


def _is_preference(key: str) -> bool:
    setting = settings_schema.BY_KEY.get(key)
    return setting is not None and setting.store_key.startswith(_PREF_PREFIX)


def _preference_keys():
    return [s.key for s in settings_schema.SETTINGS if s.store_key.startswith(_PREF_PREFIX)]


def _refusal(key: str, reason: str) -> InvalidInputError:
    """The message never echoes the offending value (it could be a pasted secret)."""
    setting = settings_schema.BY_KEY[key]
    if reason == "not true or false":
        return InvalidInputError(f"Setting '{key}' must be true or false.")
    if reason == "not an allowed choice":
        return InvalidInputError(f"'{key}' is not one of the allowed choices.")
    if reason == "not text":
        return InvalidInputError(f"'{key}' must be text.")
    if reason == "too long":
        return InvalidInputError(f"'{key}' is too long.")
    if reason == "control characters":
        return InvalidInputError(f"'{key}' contains characters that are not allowed.")
    if setting.min is None or setting.max is None:
        raise TypeError(f"'{key}' has no range to describe.")
    kind = "whole number" if setting.type == "int" else "number"
    low, high = (f"{n:g}" if n < 1e6 else str(int(n)) for n in (setting.min, setting.max))
    return InvalidInputError(f"'{key}' must be a {kind} from {low} to {high}.")


def _check_ordered_pairs(cleaned: dict) -> None:
    """A write must leave each ORDERED_PAIRS pair in order, whichever half it changes."""
    for lower, upper, why in settings_schema.ORDERED_PAIRS:
        if lower in cleaned or upper in cleaned:
            low = cleaned[lower] if lower in cleaned else get(lower)
            high = cleaned[upper] if upper in cleaned else get(upper)
            if not isinstance(low, (int, float)) or not isinstance(high, (int, float)):
                raise TypeError(f"'{lower}' and '{upper}' must be numbers.")
            if low >= high:
                raise InvalidInputError(f"'{lower}' {why} ({high:g} s).")


def _developer_mode_on() -> bool:
    # Imported here: the maintenance assistant imports this module.
    from services import assistant_settings_service
    return assistant_settings_service.developer_mode_enabled()


def set_settings(updates: dict, env_path: str | None = None) -> dict:
    """Applies a batch of non-secret toggles and preferences, then returns
    the refreshed overview. Validates everything before writing anything,
    so a bad batch changes nothing."""
    cleaned = {}
    for key, value in updates.items():
        if key in _WRITABLE_SETTINGS or key in _DB_TOGGLES or key == "gpu_max_parallel" \
                or _is_preference(key):
            if settings_schema.BY_KEY[key].dev_only and not _developer_mode_on():
                raise InvalidInputError("Turn on Developer Mode to change this setting.")
            try:
                cleaned[key] = settings_schema.validate(key, value, _choices_for(key))
            except ValueError as exc:
                raise _refusal(key, str(exc)) from None
            if key in memory_headroom.KEEP_FREE_KEYS.values():
                _check_keep_free_fits(key, cleaned[key])
        else:
            raise InvalidInputError("Unknown or non-writable setting.")
    _check_ordered_pairs(cleaned)
    for key, value in cleaned.items():
        if key == "gpu_max_parallel":
            job_settings.set_gpu_max_parallel(value)
        elif key in _WRITABLE_SETTINGS:
            _WRITABLE_SETTINGS[key](value)
        else:
            import db
            db.set_app_setting(settings_schema.BY_KEY[key].store_key, value)
    return get_settings_overview(env_path)


# --- Persisted PC-side preferences: rows of lib/settings_schema.py stored in
# db.app_settings under "pref.<name>", read through get() and written only
# through set_settings (POST /api/settings, local_only). A stored value that
# no longer validates (e.g. an engine that was removed) reads back as the default.

_PREF_PREFIX = "pref."
# The last "Test" result per engine (engine_routing_service). A key
# or endpoint write forgets it, so a stale "working" never outlives the key.
ENGINE_TEST_PREFIX = "engine_test."
LOCALE_CHOICES = ("en-US", "en-GB", "en-AU")
SUMMARY_ENGINE_CHOICES = ("claude", "deepseek", "gemini", "openai")


def _engine_choices() -> tuple:
    from engine_backends import engine_registry
    return tuple(engine_registry.ENGINES)


def engine_preference_choices() -> tuple:
    """Engines the "default engine for new dramas" preference accepts."""
    return _engine_choices()


def _ocr_choices() -> tuple:
    import ocr
    return tuple(ocr.OCR_BACKEND_OPTIONS)


def _cookie_browser_choices() -> tuple:
    import video_download
    return tuple(video_download.COOKIE_BROWSERS)


def _check_keep_free_fits(key: str, gb: float):
    """Whether it fits this PC is checked on write only: reading must not
    probe the hardware."""
    memory = "vram" if key == memory_headroom.KEEP_FREE_KEYS["vram"] else "ram"
    total = memory_headroom.total_mb(memory) if gb else None
    if total is not None and gb * 1024 > total:
        word = "graphics memory" if memory == "vram" else "RAM"
        raise InvalidInputError(f"'{key}' is more than this PC's {word} ({total / 1024:.1f} GB).")


def _speaker_model_choices() -> tuple:
    import diarize
    return diarize.SPEAKER_MODELS


def _voice_detector_choices() -> tuple:
    from services import asr_options_service
    return asr_options_service.VOICE_DETECTOR_CHOICES


REPLY_GUARD_MODES = ("auto", "on", "off")


def reasoning_override(engine_name):
    """"less" when the saved choice asks this engine's Translate runs to reason less, else
    None (the provider's default). Callers read it here so the root translate modules never
    read settings."""
    key = {"claude": "claude_reasoning", "openai": "openai_reasoning"}.get(engine_name)
    return "less" if key and get(key) == "less" else None


# The saved value tops out at 3; at that top an engine with a roomy rate limit gets
# more lanes. Below it the person asked for fewer, so that is kept.
_TOP_LANES = 3
_MORE_LANES = {"deepseek": 6}


def parallel_chapters(engine_name: str | None = None) -> int:
    """How many novel chapters one run may translate at once."""
    try:
        lanes = max(1, min(_TOP_LANES, int(str(get("novel_parallel_chapters")))))
    except (TypeError, ValueError):
        return 1
    return _MORE_LANES.get(engine_name or "", lanes) if lanes == _TOP_LANES else lanes


def reply_guard_override():
    """The translate reply guard as the pipeline's `check_replies` argument:
    None leaves the choice to the engine and model (auto), else forced on or off.
    Callers read it here so the root translate modules never read settings."""
    return {"on": True, "off": False}.get(str(get("translate_reply_guard")))


def alignment_embedder(should_stop=None, on_progress=None):
    """The embedding function for the source-match check, or None while the setting
    is off, so the root translate modules never read settings. `should_stop` ends it
    between requests; on_progress(texts done, texts) is told before each one."""
    if not get("translate_alignment_check"):
        return None
    from engine_backends import alignment_check
    base_url = ollama_url()
    return lambda texts: alignment_check.embed(base_url, texts, should_stop=should_stop,
                                               on_progress=on_progress)


_MODEL_CHECK_SECONDS = 30
_model_check = {"at": -1e9, "url": "", "present": False}


def source_match_model_present() -> bool:
    """Whether bge-m3 is pulled on the configured Ollama server (False when it does not
    answer). Cached for a short while: the queue asks under its lock on every tick and
    poll, and the Translate form on every estimate; each uncached answer is a request."""
    from engine_backends import alignment_check, ollama_server
    url = ollama_url()
    now = time.monotonic()
    if _model_check["url"] != url or now - _model_check["at"] > _MODEL_CHECK_SECONDS:
        present = (ollama_server.check_ollama_reachable(url)
                   and ollama_server.model_pulled(url, alignment_check.EMBED_MODEL))
        _model_check.update(at=now, url=url, present=present)
    return _model_check["present"]


def run_options() -> dict:
    """The saved settings a translate run records in its job args, so a run
    that is resumed or scheduled later behaves as it was submitted."""
    return {"scene_aware_batches": get("scene_aware_batches"),
            "check_replies": reply_guard_override()}


_SCHEMA_CHOICES = {
    "engines": _engine_choices,
    "locales": lambda: LOCALE_CHOICES,
    "summary_engines": lambda: SUMMARY_ENGINE_CHOICES,
    "ocr_backends": _ocr_choices,
    "cookie_browsers": _cookie_browser_choices,
    "voice_detectors": _voice_detector_choices,
    "speaker_models": _speaker_model_choices,
    "reply_guard_modes": lambda: REPLY_GUARD_MODES,
    "reasoning_modes": lambda: __import__("engine_backends.thinking", fromlist=["x"]).REASONING_MODES,
}


def _choices_for(key: str):
    setting = settings_schema.BY_KEY[key]
    return _SCHEMA_CHOICES[setting.choices]() if setting.choices else None


def read_once():
    """A block in which each stored setting is read from the database once:
    for read-only views that consult the same settings many times."""
    import db
    return db.app_settings_read_memo()


def get(key: str):
    """The one reader of a declared setting (lib/settings_schema.py), as a typed
    value. A missing, unreadable or invalid stored value reads as the schema
    default. Keys held in .env are secrets and go through resolve_key instead."""
    return stored_settings.read(key, lambda: _choices_for(key))


def is_saved(key: str) -> bool:
    """True when the person saved a value for `key`, as opposed to it reading as the schema default."""
    import db
    setting = settings_schema.BY_KEY[key]
    return setting.store == "db" and db.get_app_setting(setting.store_key) is not None


def get_preferences() -> dict:
    return {key: get(key) for key in _preference_keys()}


def _ocr_backend_status() -> dict:
    """Per concrete backend: can it run here, and if not why and which package
    fixes it. "auto" always resolves to one of these, so it has no row."""
    import ocr
    cmd = str(get("tesseract_cmd")) or None
    status = {}
    for backend in _ocr_choices():
        if backend == "auto":
            continue
        problem = ocr.backend_problem(backend, cmd)
        status[backend] = {"installed": problem is None,
                           "reason": problem.message if problem else None,
                           "package": problem.package if problem else None}
    return status


def preference_choices() -> dict:
    return {
        "engines": list(_engine_choices()),
        "locales": list(LOCALE_CHOICES),
        "summary_engines": list(SUMMARY_ENGINE_CHOICES),
        "ocr_backends": list(_ocr_choices()),
        "ocr_backend_status": _ocr_backend_status(),
        "cookie_browsers": list(_cookie_browser_choices()),
    }


def _parse_cap(raw):
    try:
        return max(0.0, float(raw)) if raw not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def get_monthly_cap_usd(env_path: str | None = None) -> float:
    """The monthly spending cap in USD, 0.0 = none: the saved Settings
    value when there is one, else BAIHE_MONTHLY_CAP_USD from .env or the
    environment."""
    saved = get("monthly_cap_usd")
    if isinstance(saved, (int, float)):
        return float(saved)
    return _parse_cap(resolve_key("monthly_cap_usd", env_path))


def month_spend_status() -> dict:
    """Numbers and a timestamp only: the full month's logged spend, the part
    of it imported with titles from another library, what the cap counts
    (since an active reset), and when that reset was made."""
    import db
    return {"month_spend_usd": db.get_month_spend(since_reset=False),
            "month_spend_imported_usd": db.get_imported_month_spend(),
            "month_spend_counted_usd": db.get_month_spend(),
            "month_spend_reset_at": db.get_month_spend_reset_at()}


def reset_month_counter() -> dict:
    """Start counting the monthly cap from now. Usage rows are kept; Undo
    clears the marker."""
    import db
    before = month_spend_status()
    db.set_app_setting(db.MONTHLY_SPEND_RESET_KEY, clock.utcnow().isoformat())
    return {"before": before, "after": month_spend_status()}


def undo_month_counter_reset() -> dict:
    import db
    before = month_spend_status()
    db.set_app_setting(db.MONTHLY_SPEND_RESET_KEY, None)
    return {"before": before, "after": month_spend_status()}


def get_cookie_settings() -> dict:
    """{cookies_browser, cookies_file} for video_download/live_translate;
    a cookies file wins over a browser there."""
    return {"cookies_browser": get("cookies_browser"),
            "cookies_file": get("cookies_file") or None}


def resolve_ocr_backend(source_language: str, allowed=None, is_installed=None) -> str:
    """The saved default OCR backend; "auto" picks by language (manga_ocr,
    or PaddleOCR-VL when preferred, for Japanese; rapidocr for zh/ko when
    installed, else paddle; tesseract otherwise -- ocr.auto_ocr_backend). `allowed` narrows
    it to what the caller supports, falling back to its first entry; so
    does an "auto" pick that `is_installed(backend)` says is missing (an
    explicit pick is kept, so the caller can say it isn't installed)."""
    backend = str(get("ocr_backend"))
    auto = backend == "auto"
    if auto:
        import ocr
        backend = ocr.auto_ocr_backend(source_language, bool(get("ocr_prefer_paddle_vl_manga")))
    if allowed and (backend not in allowed
                    or (auto and is_installed is not None and not is_installed(backend))):
        return allowed[0]
    return backend


# --- Endpoint URLs (settings parity G06, URL part). Stored in .env under
# ENV_NAMES like keys are, written through the same guarded key-write
# route shape. Not secrets, but a URL with userinfo or a query could carry
# one, so those are refused and never echoed back.

ENDPOINT_NAMES = ("ollama_url",)
_MAX_URL_LENGTH = 300


def validate_endpoint_url(value) -> str:
    """Returns the stripped URL (no trailing slash) or raises
    InvalidInputError. http(s) only, a host, no userinfo, query or
    fragment, and nothing that could break or inject a .env line."""
    from urllib.parse import urlsplit
    if not isinstance(value, str):
        raise InvalidInputError("The URL must be text.")
    value = value.strip()
    if not value:
        raise InvalidInputError("The URL is empty.")
    if len(value) > _MAX_URL_LENGTH:
        raise InvalidInputError("The URL is too long.")
    if any(ord(c) < 33 or ord(c) == 127 or c in "\"'\\" for c in value):
        raise InvalidInputError("The URL contains characters that are not allowed.")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        raise InvalidInputError("The URL is not valid.") from None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise InvalidInputError("The URL must start with http:// or https:// and name a host.")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise InvalidInputError("The URL must not contain a user name or password.")
    if parts.query or parts.fragment or "?" in value or "#" in value:
        raise InvalidInputError("The URL must not contain a query or fragment.")
    del port
    return value.rstrip("/")


def endpoint_values(env_path: str | None = None) -> dict:
    """{name: url or None}. A configured value that fails validation (for
    example a hand-edited .env line with a password in it) reads as None
    here; engine_keys still reports it as configured."""
    out = {}
    for name in ENDPOINT_NAMES:
        raw = resolve_key(name, env_path)
        if raw and name == "ollama_url":
            # What the app really uses, not a hand-edited "localhost:11434".
            raw = settings_schema.normalize_ollama_url(raw)
        try:
            out[name] = validate_endpoint_url(raw) if raw else None
        except InvalidInputError:
            out[name] = None
    return out


def _validate_endpoint_name(name: str):
    if name not in ENDPOINT_NAMES:
        raise InvalidInputError("Unknown endpoint.")


def set_endpoint_url(name: str, value: str, env_path: str | None = None) -> dict:
    _validate_endpoint_name(name)
    if name == "ollama_url" and isinstance(value, str) and value.strip():
        value = settings_schema.normalize_ollama_url(value)
    value = validate_endpoint_url(value)
    env_path = env_path or env_reader.default_env_path()
    write_env_var(ENV_NAMES[name][0], value, env_path)
    _forget_engine_test(name)
    return {"name": name, "url": endpoint_values(env_path)[name],
            "configured": bool(resolve_key(name, env_path))}


def clear_endpoint_url(name: str, env_path: str | None = None) -> dict:
    """Removes the endpoint from .env. One set in the real environment
    stays in effect; the result reports what now applies."""
    _validate_endpoint_name(name)
    env_path = env_path or env_reader.default_env_path()
    remove_env_vars(ENV_NAMES[name], env_path)
    _forget_engine_test(name)
    return {"name": name, "url": endpoint_values(env_path)[name],
            "configured": bool(resolve_key(name, env_path))}


# Write-only secret keys. URL settings and the numeric cap are
# not secrets and stay out; only real keys/tokens can be set here.
KEY_WRITE_ENGINES = ("claude", "deepseek", "gemini", "openai", "groq", "hf_token")
_MAX_KEY_LENGTH = 512


def _validate_engine(engine: str):
    if engine not in KEY_WRITE_ENGINES:
        raise InvalidInputError("Unknown engine.")


def _validate_key_value(value) -> str:
    """Returns the stripped key or raises InvalidInputError. Messages never
    echo the value. Rejects anything that could inject another .env line
    or be mangled by the .env reader (control characters, inner
    whitespace, quotes)."""
    if not isinstance(value, str):
        raise InvalidInputError("The key must be text.")
    value = value.strip()
    if not value:
        raise InvalidInputError("The key is empty.")
    if len(value) > _MAX_KEY_LENGTH:
        raise InvalidInputError("The key is too long.")
    if any(ord(c) < 33 or ord(c) == 127 or c in "\"'" or c.isspace() for c in value):
        raise InvalidInputError("The key contains characters that are not allowed.")
    return value


_ENV_LOCK = threading.Lock()


def _rewrite_env(env_path: str, transform):
    with _ENV_LOCK:
        _rewrite_env_locked(env_path, transform)


def _rewrite_env_locked(env_path: str, transform):
    """Atomically rewrites the .env file: `transform(lines)` returns the
    new line list. Temp file in the same folder + os.replace; the result is
    always mode 0600."""
    import tempfile
    lines = []
    # Always 0600, not the old file's mode: a hand-made world-readable .env
    # must not stay readable just because it was saved through the app.
    mode = 0o600
    if os.path.exists(env_path):
        # surrogateescape round-trips bytes we cannot decode, so saving a key never rewrites them.
        # A UTF-16 file is decoded as such and saved as UTF-8 like every other.
        encoding = env_reader.file_encoding(env_path)
        with open(env_path, encoding=encoding,
                  errors="surrogateescape" if encoding == "utf-8-sig" else "replace") as fh:
            lines = fh.readlines()
    new_lines = transform(lines)
    folder = os.path.dirname(os.path.abspath(env_path))
    fd, tmp = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", errors="surrogateescape", newline="") as fh:
            fh.writelines(new_lines)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        try:
            os.chmod(tmp, mode)
        except OSError:
            pass
        os.replace(tmp, env_path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _line_var(line: str) -> str:
    return line.strip().split("=", 1)[0].strip()


def set_engine_key(engine: str, value: str, env_path: str | None = None) -> dict:
    """Writes `value` to .env under the engine's canonical name (the first
    ENV_NAMES entry, the one resolve_key reads first), preserving other
    lines.
    Returns {engine, configured} only -- never the value."""
    _validate_engine(engine)
    value = _validate_key_value(value)
    env_path = env_path or env_reader.default_env_path()
    write_env_var(ENV_NAMES[engine][0], value, env_path)
    _forget_engine_test(engine)
    return {"engine": engine, "configured": bool(resolve_key(engine, env_path))}


def write_env_var(var_name: str, value: str, env_path: str | None = None):
    """Sets `var_name=value` in .env in place (appends when absent),
    keeping every other line. `value` must already be validated (no
    whitespace, quotes or control characters)."""
    env_path = env_path or env_reader.default_env_path()

    def transform(lines):
        new_line = f"{var_name}={value}\n"
        out, done = [], False
        for line in lines:
            if _line_var(line) == var_name:
                if not done:
                    out.append(new_line)
                    done = True
                continue
            out.append(line)
        if not done:
            if out and not out[-1].endswith("\n"):
                out[-1] += "\n"
            out.append(new_line)
        return out

    _rewrite_env(env_path, transform)


def remove_env_vars(names, env_path: str | None = None):
    """Removes every line setting one of `names` from .env (if it exists)."""
    env_path = env_path or env_reader.default_env_path()
    names = set(names)
    if os.path.exists(env_path):
        _rewrite_env(env_path, lambda lines: [l for l in lines if _line_var(l) not in names])


def clear_engine_key(engine: str, env_path: str | None = None) -> dict:
    """Removes the engine's key lines from .env (every accepted name for
    it). A key that also comes from a real environment variable stays
    configured -- `configured` reports the truth."""
    _validate_engine(engine)
    env_path = env_path or env_reader.default_env_path()
    remove_env_vars(ENV_NAMES[engine], env_path)
    _forget_engine_test(engine)
    return {"engine": engine, "configured": bool(resolve_key(engine, env_path))}


ENGINE_TEST_GENERATION_PREFIX = "engine_test_gen."


def engine_test_generation(engine: str) -> int:
    """Bumped on every key/endpoint write for `engine`, so a Test that was
    already running when the key changed doesn't record its stale result."""
    import db
    try:
        value = db.get_app_setting(ENGINE_TEST_GENERATION_PREFIX + engine, 0)
    except sqlite3.Error:
        return 0
    return value if isinstance(value, int) else 0


def _forget_engine_test(name: str):
    """Drops the saved Test result for the engine a key or endpoint belongs
    to, e.g. "ollama_url" -> "ollama". Best effort: the key or URL
    is already written to .env, and status bookkeeping must never turn that
    into an error (e.g. a library whose tables don't exist yet)."""
    import db
    engine = name[:-len("_url")] if name.endswith("_url") else name
    try:
        db.set_app_setting(ENGINE_TEST_GENERATION_PREFIX + engine,
                           engine_test_generation(engine) + 1)
        db.set_app_setting(ENGINE_TEST_PREFIX + engine, None)
    except sqlite3.Error:
        pass
