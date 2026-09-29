"""
services/settings_service.py -- Streamlit-free settings resolution shared
between the Settings sidebar (tabs/settings_tab.py) and the FastAPI
settings endpoint (api/routers/settings_routes.py), so both read the same
env-var mapping instead of maintaining two copies that could drift.

Per D2 (docs/migration-review.md §6): API keys are server-side only.
resolve_key() is for server-side use (e.g. a future translate route) --
never return its result over an HTTP response. key_status() and
get_settings_overview() are what an API route may expose: booleans only.
"""
import os
import threading
from typing import Optional

import background_jobs
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
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")


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


def resolve_gemini_free_tier(value) -> bool:
    """A request's gemini_free_tier: None (omitted) means the persisted
    setting; an explicit True/False is kept."""
    return get_gemini_free_tier() if value is None else bool(value)


def get_settings_overview(env_path: str = None) -> dict:
    """Non-secret settings snapshot for the FastAPI settings endpoint."""
    return {
        "engine_keys": key_status(env_path),
        "gpu_limit_enabled": background_jobs.get_gpu_limit_enabled(),
        "notify_on_completion": background_jobs.get_notify_on_completion(),
        "use_gpu": get_use_gpu(),
        "gemini_free_tier": get_gemini_free_tier(),
    }


def _set_app_bool(key: str, enabled: bool):
    import db
    db.set_app_setting(key, bool(enabled))


# Typed allow-list of writable, non-secret settings (Slice 23). Keys, URLs
# and paths are deliberately NOT here (D2) -- key writes are a separate
# gated slice.
_WRITABLE_SETTINGS = {
    "gpu_limit_enabled": background_jobs.set_gpu_limit_enabled,
    "notify_on_completion": background_jobs.set_notify_on_completion,
    "use_gpu": lambda v: _set_app_bool("use_gpu", v),
    "gemini_free_tier": lambda v: _set_app_bool("gemini_free_tier", v),
}


def set_settings(updates: dict, env_path: str = None) -> dict:
    """Applies a batch of non-secret boolean toggles, then returns the
    refreshed overview. Validates everything before writing anything, so a
    bad batch changes nothing. Error messages never echo the offending
    value (it could be a pasted secret)."""
    for key, value in updates.items():
        if key not in _WRITABLE_SETTINGS:
            raise InvalidInputError("Unknown or non-writable setting.")
        if not isinstance(value, bool):
            raise InvalidInputError(f"Setting '{key}' must be true or false.")
    for key, value in updates.items():
        _WRITABLE_SETTINGS[key](value)
    return get_settings_overview(env_path)


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
    return {"engine": engine, "configured": bool(resolve_key(engine, env_path))}
