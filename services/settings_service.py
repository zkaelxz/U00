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
from typing import Optional

import background_jobs

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


def _read_env_file(env_path: str = None) -> dict:
    """Parses the project's .env file the same way tabs/settings_tab.py's
    _load_env_defaults() does: utf-8-sig (BOM-safe, since Notepad-saved
    .env files often carry one), skips comments/blank lines, strips
    surrounding quotes. Returns {} if the file is missing or malformed --
    a broken .env should never crash a caller.
    """
    env = {}
    if env_path is None:
        env_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
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
    env = _read_env_file(env_path)
    for name in ENV_NAMES.get(settings_key, ()):
        val = env.get(name) or os.environ.get(name)
        if val:
            return val
    return None


def key_status(env_path: str = None) -> dict:
    """{settings_key: bool} for every engine key/endpoint setting --
    whether a value is configured, never the value itself (D2)."""
    return {key: bool(resolve_key(key, env_path)) for key in _ENGINE_KEY_NAMES}


def get_settings_overview(env_path: str = None) -> dict:
    """Non-secret settings snapshot for the FastAPI settings endpoint."""
    return {
        "engine_keys": key_status(env_path),
        "gpu_limit_enabled": background_jobs.get_gpu_limit_enabled(),
        "notify_on_completion": background_jobs.get_notify_on_completion(),
    }
