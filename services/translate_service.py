"""
services/translate_service.py -- Streamlit-free, read-only metadata for
Migration Slice 11 (Phase 5, docs/migration-review.md section 3.7): the
"list engines" / "list history" half of the standalone translate tool
(tabs/translate_tab.py). Reuses translate_engines' own ENGINES/model dicts
and services.settings_service's key_status() rather than reimplementing key
resolution -- per D2 (docs/migration-review.md section 6), a resolved key
value itself must never be returned from here.

Deliberately NOT included (deferred to a later, separate slice):
  - actually performing a translation (translate_engines.standalone_translate)
    -- a real network call, kept out of this read-only slice on purpose.
  - clearing history (db.clear_translate_history) -- a write action.
"""
from typing import Optional

import db
import translate_engines
from services import settings_service

# translate_engines.ENGINES keys whose engine class needs an API key to run
# at all (see translate_engines.py's own ENGINES / FREE_ENGINES and
# tabs/translate_tab.py's api_key handling for the reasoning below):
#   - test_offline: TestOfflineEngine needs no key at all (dry-run only).
#   - nllb: a locally-downloaded model, no key at all.
#   - ollama / libretranslate: tabs/translate_tab.py treats their key field
#     as optional, defaulting to the literal "local" when nothing is
#     entered -- both point at a locally-run server, not a hosted API that
#     requires an account key. There's nothing meaningful to "configure" in
#     the same sense as an API key, so they're reported as configured too.
# Everything else (claude/deepseek/gemini/deepl/google) maps directly onto
# services.settings_service.key_status(), which is keyed by the same engine
# name for these five.
_NO_KEY_REQUIRED_ENGINES = {"test_offline", "nllb", "ollama", "libretranslate"}

# Engine name -> the model dict (if any) tabs/translate_tab.py lets the user
# pick a model from for that engine.
_ENGINE_MODEL_DICTS = {
    "claude": translate_engines.CLAUDE_MODELS,
    "gemini": translate_engines.GEMINI_MODELS,
    "ollama": translate_engines.OLLAMA_MODELS,
    "nllb": translate_engines.NLLB_MODELS,
}


def list_engines(env_path: Optional[str] = None) -> list:
    """One entry per translate_engines.ENGINES key: name, display label,
    whether it's free (translate_engines.FREE_ENGINES), its selectable
    model keys (or None if the engine has no model picker), and whether a
    key/endpoint is configured for it (never the key value itself -- see
    services.settings_service.key_status)."""
    key_status = settings_service.key_status(env_path)
    engines = []
    for name in translate_engines.ENGINES:
        model_dict = _ENGINE_MODEL_DICTS.get(name)
        if name in _NO_KEY_REQUIRED_ENGINES:
            key_configured = True
        else:
            key_configured = bool(key_status.get(name, False))
        engines.append({
            "name": name,
            "label": translate_engines.engine_picker_label(name),
            "free": name in translate_engines.FREE_ENGINES,
            "models": list(model_dict.keys()) if model_dict is not None else None,
            "key_configured": key_configured,
        })
    return engines


def list_history(limit: int = 50) -> list:
    """Thin wrapper over db.list_translate_history -- most recent first."""
    return db.list_translate_history(limit=limit)
