"""
services/settings_service.py -- Streamlit-free settings resolution shared
between the Settings sidebar (tabs/settings_tab.py) and the FastAPI
settings endpoint (api/routers/settings_routes.py), so both read the same
env-var mapping instead of maintaining two copies that could drift.

Per D2 (docs/archive/migration-review.md §6): API keys are server-side only.
resolve_key() is for server-side use (e.g. a future translate route) --
never return its result over an HTTP response. key_status() and
get_settings_overview() are what an API route may expose: booleans only.
"""
import os
import sqlite3
import threading
from typing import Optional

import background_jobs
import portable
from services.service_errors import InvalidInputError

# Per settings key, the env var name(s) to read, in priority order -- the
# first entry is also the canonical name tabs/settings_tab.py's
# save_key_to_env() writes back, so a saved key round-trips through the
# exact same name it would be read back under. Kept in sync with
# tabs/settings_tab.py, which imports this dict rather than keeping its
# own copy.
ENV_NAMES = {
    "claude": ("BAIHE_CLAUDE_KEY", "ANTHROPIC_API_KEY"),
    "deepseek": ("BAIHE_DEEPSEEK_KEY", "DEEPSEEK_API_KEY"),
    # Deliberately NOT falling back to GOOGLE_API_KEY here -- that name
    # is already claimed by the separate Google Translate engine below,
    # and a Cloud Translation key isn't guaranteed to also work as a
    # Gemini API key (different products, often different projects).
    "gemini": ("BAIHE_GEMINI_KEY", "GEMINI_API_KEY"),
    "deepl": ("BAIHE_DEEPL_KEY", "DEEPL_API_KEY"),
    "google": ("BAIHE_GOOGLE_KEY", "GOOGLE_API_KEY"),
    "groq": ("BAIHE_GROQ_KEY", "GROQ_API_KEY"),
    "hf_token": ("BAIHE_HF_TOKEN", "HF_TOKEN", "HUGGINGFACE_TOKEN"),
    "ollama_url": ("BAIHE_OLLAMA_URL",),
    "libretranslate_url": ("BAIHE_LIBRETRANSLATE_URL",),
    "gpt_sovits_url": ("BAIHE_GPT_SOVITS_URL",),
    "monthly_cap_usd": ("BAIHE_MONTHLY_CAP_USD",),
}

# The subset of ENV_NAMES that are actual keys/endpoints (not a numeric
# setting like monthly_cap_usd) -- what an API overview reports presence
# for.
_ENGINE_KEY_NAMES = tuple(k for k in ENV_NAMES if k != "monthly_cap_usd")


def _default_env_path() -> str:
    # The project folder for a source checkout; the per-user data folder
    # for an installed copy, so keys never sit in the program files an
    # update replaces (portable.data_dir(), Step 80b).
    return os.path.join(portable.data_dir(), ".env")


def _read_env_file(env_path: str = None) -> dict:
    """Parses the project's .env file the same way tabs/settings_tab.py's
    _load_env_defaults() does: utf-8-sig (BOM-safe, since Notepad-saved
    .env files often carry one), skips comments/blank lines, strips
    surrounding quotes. Returns {} if the file is missing or malformed --
    a broken .env should never crash a caller.
    """
    env = {}
    if env_path is None:
        env_path = _default_env_path()
    if os.path.exists(env_path):
        try:
            with open(env_path, encoding="utf-8-sig") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
        except Exception:
            pass
    return env


def resolve_key(settings_key: str, env_path: str = None) -> Optional[str]:
    """Server-side only: resolves settings_key's value from .env, then
    real environment variables, in ENV_NAMES' priority order. Never expose
    the return value over an API response -- callers that report status
    over HTTP must use key_status()/get_settings_overview() instead.
    """
    return resolve_env_names(ENV_NAMES.get(settings_key, ()), env_path)


def resolve_env_names(names, env_path: str = None) -> Optional[str]:
    """Server-side only: the first non-empty value among `names`, from .env
    then real environment variables. Shared with notification_service's
    webhook/topic secrets, which are not engine keys."""
    env = _read_env_file(env_path)
    for name in names:
        val = env.get(name) or os.environ.get(name)
        if val:
            return val
    return None


# Default API port (api.api_config.DEFAULT_PORT), Streamlit's 8501 and the
# extension bridge's 8756 (page_server.DEFAULT_PORT). Services may not import
# api/, so the numbers are repeated here.
_BAIHE_FIXED_PORTS = (8501, 8600, 8756)
API_PORT_ENV = "BAIHE_API_PORT"
HOUSEHOLD_PORT_ENV = "BAIHE_API_HOUSEHOLD_PORT"


def baihe_own_ports() -> set:
    """Ports on this PC that are Baihe's own, which outbound features (ntfy,
    SearXNG, Jellyfin) must not be pointed at: the fixed ones above plus the
    configured BAIHE_API_PORT and BAIHE_API_HOUSEHOLD_PORT. Both the real
    environment and .env are read, so a port set in either is protected."""
    ports = set(_BAIHE_FIXED_PORTS)
    env = _read_env_file()
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
    import page_server
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


def key_status(env_path: str = None) -> dict:
    """{settings_key: bool} for every engine key/endpoint setting --
    whether a value is configured, never the value itself (D2)."""
    return {key: bool(resolve_key(key, env_path)) for key in _ENGINE_KEY_NAMES}


def _get_bool_setting(key: str) -> bool:
    import db
    try:
        return bool(db.get_app_setting(key, False))
    except Exception:
        return False


def get_use_gpu() -> bool:
    """Persisted server-side GPU toggle for GPU-capable API jobs (Slice
    23). Default False; a DB hiccup fails closed (CPU)."""
    return _get_bool_setting("use_gpu")


def get_gemini_free_tier() -> bool:
    """Persisted 'Gemini is on the free tier' flag (Slice 23). Default False."""
    return _get_bool_setting("gemini_free_tier")


def get_bulk_auto_resume() -> bool:
    """Opt-in: resume interrupted bulk translation batches when the API
    starts. Default False (a resumed batch can spend on the engine account)."""
    return _get_bool_setting(BULK_AUTO_RESUME_KEY)


def resolve_gemini_free_tier(value) -> bool:
    """A request's gemini_free_tier: None (omitted) means the persisted
    setting; an explicit True/False is kept."""
    return get_gemini_free_tier() if value is None else bool(value)


def get_settings_overview(env_path: str = None) -> dict:
    """Non-secret settings snapshot for the FastAPI settings endpoint.
    Preferences hold paths (never a file's contents); endpoints hold only
    URLs that pass validate_endpoint_url (no userinfo, query or fragment),
    so neither can carry a secret."""
    return {
        "engine_keys": key_status(env_path),
        "gpu_limit_enabled": background_jobs.get_gpu_limit_enabled(),
        "notify_on_completion": background_jobs.get_notify_on_completion(),
        "use_gpu": get_use_gpu(),
        "gemini_free_tier": get_gemini_free_tier(),
        "bulk_auto_resume": get_bulk_auto_resume(),
        "preferences": get_preferences(),
        "endpoints": endpoint_values(env_path),
        "monthly_cap_env_usd": _parse_cap(resolve_key("monthly_cap_usd", env_path)),
        "effective_monthly_cap_usd": get_monthly_cap_usd(env_path),
        "choices": preference_choices(),
    }


BULK_AUTO_RESUME_KEY = "bulk.auto_resume"


def _set_app_bool(key: str, enabled: bool):
    import db
    db.set_app_setting(key, bool(enabled))


# Typed allow-list of writable, non-secret boolean settings (Slice 23).
# Keys are never here (D2); preferences (paths, defaults, the cap) are in
# _PREFERENCES below, endpoint URLs go through set_endpoint_url.
_WRITABLE_SETTINGS = {
    "gpu_limit_enabled": background_jobs.set_gpu_limit_enabled,
    "notify_on_completion": background_jobs.set_notify_on_completion,
    "use_gpu": lambda v: _set_app_bool("use_gpu", v),
    "gemini_free_tier": lambda v: _set_app_bool("gemini_free_tier", v),
    "bulk_auto_resume": lambda v: _set_app_bool(BULK_AUTO_RESUME_KEY, v),
}


def set_settings(updates: dict, env_path: str = None) -> dict:
    """Applies a batch of non-secret toggles and preferences, then returns
    the refreshed overview. Validates everything before writing anything,
    so a bad batch changes nothing. Error messages never echo the
    offending value (it could be a pasted secret)."""
    cleaned = {}
    for key, value in updates.items():
        if key in _WRITABLE_SETTINGS:
            if not isinstance(value, bool):
                raise InvalidInputError(f"Setting '{key}' must be true or false.")
            cleaned[key] = value
        elif key in _PREFERENCES:
            cleaned[key] = _PREFERENCES[key][1](value)
        else:
            raise InvalidInputError("Unknown or non-writable setting.")
    for key, value in cleaned.items():
        if key in _WRITABLE_SETTINGS:
            _WRITABLE_SETTINGS[key](value)
        else:
            import db
            db.set_app_setting(_PREF_PREFIX + key, value)
    return get_settings_overview(env_path)


# --- Persisted PC-side preferences (settings parity G05, G08, G09, G13,
# G14, G15). Streamlit kept these in session state only; here they live in
# db.app_settings under "pref.<name>" and the services that use them read
# them through the getters below. Written only through set_settings
# (POST /api/settings, local_only). A stored value that no longer
# validates (e.g. an engine that was removed) reads back as the default.

_PREF_PREFIX = "pref."
# Step 36: the last "Test" result per engine (engine_routing_service). A key
# or endpoint write forgets it, so a stale "working" never outlives the key.
ENGINE_TEST_PREFIX = "engine_test."
_MAX_PATH_LENGTH = 1024
_MAX_STYLE_NOTE_LENGTH = 2000
_MAX_NUM_CTX = 1_048_576
_MAX_MONTHLY_CAP = 1_000_000.0
LOCALE_CHOICES = ("en-US", "en-GB", "en-AU")
SUMMARY_ENGINE_CHOICES = ("ollama", "claude", "deepseek", "gemini")


def _engine_choices() -> tuple:
    import translate_engines
    return tuple(k for k in translate_engines.ENGINES if k != "test_offline")


def engine_preference_choices() -> tuple:
    """Engines the "default engine for new dramas" preference accepts."""
    return _engine_choices()


def _ocr_choices() -> tuple:
    import ocr
    return tuple(ocr.OCR_BACKEND_OPTIONS)


def _cookie_browser_choices() -> tuple:
    import video_download
    return tuple(video_download.COOKIE_BROWSERS)


def _one_of(name, choices_fn):
    def check(value):
        if not isinstance(value, str) or value not in choices_fn():
            raise InvalidInputError(f"'{name}' is not one of the allowed choices.")
        return value
    return check


def _optional_one_of(name, choices_fn):
    inner = _one_of(name, choices_fn)

    def check(value):
        return None if value is None or value == "" else inner(value)
    return check


def _check_bool(name):
    def check(value):
        if not isinstance(value, bool):
            raise InvalidInputError(f"Setting '{name}' must be true or false.")
        return value
    return check


def _check_text(name, max_len, multiline=False):
    def check(value):
        if value is None:
            return ""
        if not isinstance(value, str):
            raise InvalidInputError(f"'{name}' must be text.")
        value = value.strip()
        if len(value) > max_len:
            raise InvalidInputError(f"'{name}' is too long.")
        allowed = "\n\t" if multiline else ""
        if any((ord(c) < 32 and c not in allowed) or ord(c) == 127 for c in value):
            raise InvalidInputError(f"'{name}' contains characters that are not allowed.")
        return value
    return check


def _check_int(name, low, high):
    def check(value):
        if value is None:
            return 0
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise InvalidInputError(f"'{name}' must be a whole number from {low} to {high}.")
        return value
    return check


def _check_cap(value):
    """None clears the saved cap (BAIHE_MONTHLY_CAP_USD in .env applies
    again); 0 means no cap."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value \
            or not 0 <= value <= _MAX_MONTHLY_CAP:
        raise InvalidInputError("'monthly_cap_usd' must be a number from 0 to 1000000.")
    return float(value)


# name -> (default, validator). The validator returns the cleaned value
# or raises InvalidInputError without echoing the input.
_PREFERENCES = {
    "default_engine": ("claude", _one_of("default_engine", _engine_choices)),
    "default_locale": ("en-US", _one_of("default_locale", lambda: LOCALE_CHOICES)),
    "default_style_note": ("", _check_text("default_style_note", _MAX_STYLE_NOTE_LENGTH,
                                           multiline=True)),
    "episode_summary_engine": ("ollama", _one_of("episode_summary_engine",
                                                 lambda: SUMMARY_ENGINE_CHOICES)),
    "monthly_cap_usd": (None, _check_cap),
    "ollama_num_ctx_override": (0, _check_int("ollama_num_ctx_override", 0, _MAX_NUM_CTX)),
    "whisper_model_path": ("", _check_text("whisper_model_path", _MAX_PATH_LENGTH)),
    "ocr_backend": ("auto", _one_of("ocr_backend", _ocr_choices)),
    "ocr_prefer_paddle_vl_manga": (False, _check_bool("ocr_prefer_paddle_vl_manga")),
    # Accepted risk (inventory G05): the program Tesseract runs is editable
    # here. Writes are PC-only, like every other settings write.
    "tesseract_cmd": ("", _check_text("tesseract_cmd", _MAX_PATH_LENGTH)),
    # Step 115b: the lightnovel-crawler program, when it isn't on PATH. Same
    # accepted risk as tesseract_cmd; services/lncrawl_service.py also only
    # runs a file named lncrawl / lightnovel-crawler.
    "lncrawl_cmd": ("", _check_text("lncrawl_cmd", _MAX_PATH_LENGTH)),
    "cookies_browser": (None, _optional_one_of("cookies_browser", _cookie_browser_choices)),
    # A path only; the file's contents are never read or returned here.
    "cookies_file": ("", _check_text("cookies_file", _MAX_PATH_LENGTH)),
}


def preference_default(name: str):
    return _PREFERENCES[name][0]


def get_preference(name: str):
    default, check = _PREFERENCES[name]
    import db
    try:
        raw = db.get_app_setting(_PREF_PREFIX + name, default)
        return check(raw)
    except Exception:
        return default


def get_preferences() -> dict:
    return {name: get_preference(name) for name in _PREFERENCES}


def preference_choices() -> dict:
    return {
        "engines": list(_engine_choices()),
        "locales": list(LOCALE_CHOICES),
        "summary_engines": list(SUMMARY_ENGINE_CHOICES),
        "ocr_backends": list(_ocr_choices()),
        "cookie_browsers": list(_cookie_browser_choices()),
    }


def _parse_cap(raw):
    try:
        return max(0.0, float(raw)) if raw not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def get_monthly_cap_usd(env_path: str = None) -> float:
    """The monthly spending cap in USD, 0.0 = none: the saved Settings
    value when there is one, else BAIHE_MONTHLY_CAP_USD from .env or the
    environment."""
    saved = get_preference("monthly_cap_usd")
    if saved is not None:
        return float(saved)
    return _parse_cap(resolve_key("monthly_cap_usd", env_path))


def get_default_engine() -> str:
    """The translation engine for a drama with none saved (Settings >
    Defaults for new dramas; "claude" until changed)."""
    return get_preference("default_engine")


def get_ollama_num_ctx_override() -> int:
    """0 = size the context window from the prompt (the default)."""
    return get_preference("ollama_num_ctx_override")


def get_whisper_model_path():
    return get_preference("whisper_model_path") or None


def get_tesseract_cmd():
    return get_preference("tesseract_cmd") or None


def get_lncrawl_cmd():
    return get_preference("lncrawl_cmd") or None


def get_cookie_settings() -> dict:
    """{cookies_browser, cookies_file} for video_download/live_translate;
    a cookies file wins over a browser there."""
    return {"cookies_browser": get_preference("cookies_browser"),
            "cookies_file": get_preference("cookies_file") or None}


def resolve_ocr_backend(source_language: str, allowed=None, is_installed=None) -> str:
    """The saved default OCR backend; "auto" picks by language (manga_ocr,
    or PaddleOCR-VL when preferred, for Japanese; paddle for zh/ko;
    tesseract otherwise -- scanlate.auto_ocr_backend). `allowed` narrows
    it to what the caller supports, falling back to its first entry; so
    does an "auto" pick that `is_installed(backend)` says is missing (an
    explicit pick is kept, so the caller can say it isn't installed)."""
    backend = get_preference("ocr_backend")
    auto = backend == "auto"
    if auto:
        backend = _auto_ocr_backend(source_language, get_preference("ocr_prefer_paddle_vl_manga"))
    if allowed and (backend not in allowed
                    or (auto and is_installed is not None and not is_installed(backend))):
        return allowed[0]
    return backend


def _auto_ocr_backend(source_language: str, prefer_paddle_vl_manga: bool) -> str:
    # Same rule as scanlate.auto_ocr_backend, without importing scanlate
    # (it pulls in OpenCV/numpy for a two-line lookup).
    if source_language == "ja":
        return "paddle_vl_manga" if prefer_paddle_vl_manga else "manga_ocr"
    if source_language in ("zh", "ko"):
        return "paddle"
    return "tesseract"


# --- Endpoint URLs (settings parity G06, URL part). Stored in .env under
# ENV_NAMES like keys are, written through the same guarded key-write
# route shape. Not secrets, but a URL with userinfo or a query could carry
# one, so those are refused and never echoed back.

ENDPOINT_NAMES = ("ollama_url", "libretranslate_url", "gpt_sovits_url")
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


def endpoint_values(env_path: str = None) -> dict:
    """{name: url or None}. A configured value that fails validation (for
    example a hand-edited .env line with a password in it) reads as None
    here; engine_keys still reports it as configured."""
    out = {}
    for name in ENDPOINT_NAMES:
        raw = resolve_key(name, env_path)
        try:
            out[name] = validate_endpoint_url(raw) if raw else None
        except InvalidInputError:
            out[name] = None
    return out


def _validate_endpoint_name(name: str):
    if name not in ENDPOINT_NAMES:
        raise InvalidInputError("Unknown endpoint.")


def set_endpoint_url(name: str, value: str, env_path: str = None) -> dict:
    _validate_endpoint_name(name)
    value = validate_endpoint_url(value)
    env_path = env_path or _default_env_path()
    write_env_var(ENV_NAMES[name][0], value, env_path)
    _forget_engine_test(name)
    return {"name": name, "url": endpoint_values(env_path)[name],
            "configured": bool(resolve_key(name, env_path))}


def clear_endpoint_url(name: str, env_path: str = None) -> dict:
    """Removes the endpoint from .env. One set in the real environment
    stays in effect; the result reports what now applies."""
    _validate_endpoint_name(name)
    env_path = env_path or _default_env_path()
    remove_env_vars(ENV_NAMES[name], env_path)
    _forget_engine_test(name)
    return {"name": name, "url": endpoint_values(env_path)[name],
            "configured": bool(resolve_key(name, env_path))}


# Slice 24: write-only secret keys. URL settings and the numeric cap are
# not secrets and stay out; only real keys/tokens can be set here.
KEY_WRITE_ENGINES = ("claude", "deepseek", "gemini", "deepl", "google", "groq", "hf_token")
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
    new line list. Temp file in the same folder + os.replace; keeps the
    existing file's permission bits (new files get 0600)."""
    import tempfile
    lines = []
    mode = 0o600
    if os.path.exists(env_path):
        mode = os.stat(env_path).st_mode & 0o777
        with open(env_path, encoding="utf-8-sig") as fh:
            lines = fh.readlines()
    new_lines = transform(lines)
    folder = os.path.dirname(os.path.abspath(env_path))
    fd, tmp = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
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


def set_engine_key(engine: str, value: str, env_path: str = None) -> dict:
    """Writes `value` to .env under the engine's canonical name (same name
    tabs/settings_tab.save_key_to_env uses), preserving other lines.
    Returns {engine, configured} only -- never the value."""
    _validate_engine(engine)
    value = _validate_key_value(value)
    env_path = env_path or _default_env_path()
    write_env_var(ENV_NAMES[engine][0], value, env_path)
    _forget_engine_test(engine)
    return {"engine": engine, "configured": bool(resolve_key(engine, env_path))}


def write_env_var(var_name: str, value: str, env_path: str = None):
    """Sets `var_name=value` in .env in place (appends when absent),
    keeping every other line. `value` must already be validated (no
    whitespace, quotes or control characters)."""
    env_path = env_path or _default_env_path()

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


def remove_env_vars(names, env_path: str = None):
    """Removes every line setting one of `names` from .env (if it exists)."""
    env_path = env_path or _default_env_path()
    names = set(names)
    if os.path.exists(env_path):
        _rewrite_env(env_path, lambda lines: [l for l in lines if _line_var(l) not in names])


def clear_engine_key(engine: str, env_path: str = None) -> dict:
    """Removes the engine's key lines from .env (every accepted name for
    it). A key that also comes from a real environment variable stays
    configured -- `configured` reports the truth."""
    _validate_engine(engine)
    env_path = env_path or _default_env_path()
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
    to (Step 36), e.g. "ollama_url" -> "ollama". Best effort: the key or URL
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
