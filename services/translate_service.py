"""
services/translate_service.py -- Streamlit-free metadata and translate
action for the standalone translate tool (tabs/translate_tab.py).
Migration Slice 11 (Phase 5, docs/migration-review.md section 3.7) added
the read-only "list engines" / "list history" half; Migration Slice 13
adds translate() itself, resolving a server-side key per engine (D2 --
docs/migration-review.md section 6) rather than accepting one from the
caller.

Deliberately NOT included (deferred to a later, separate slice):
  - clearing history (db.clear_translate_history) -- a write action.
"""
from typing import Optional

import db
import translate_engines
from services import settings_service
from services.service_errors import (DependencyUnavailableError, InvalidInputError,
                                      UnsupportedOperationError)

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


def _resolve_api_key(engine_name: str, env_path: Optional[str] = None) -> Optional[str]:
    """The literal value to pass into translate_engines.get_engine, per
    engine (see tabs/translate_tab.py's own api_key handling, lines
    79-92, for the exact behavior this mirrors):
      - test_offline: the literal "offline" -- TestOfflineEngine ignores it.
      - nllb: None -- a locally-downloaded model, nothing to pass.
      - ollama/libretranslate: a resolved key/URL if configured, else the
        literal "local" (both point at a locally-run server).
      - everything else: whatever services.settings_service.resolve_key
        finds, or None if nothing is configured.
    """
    if engine_name == "test_offline":
        return "offline"
    if engine_name == "nllb":
        return None
    if engine_name in ("ollama", "libretranslate"):
        return settings_service.resolve_key(engine_name, env_path) or "local"
    return settings_service.resolve_key(engine_name, env_path)


def translate(text: str, engine_name: str, source_language: str, target_language: str,
              model: Optional[str] = None, free_tier: bool = False,
              base_url: Optional[str] = None, env_path: Optional[str] = None) -> dict:
    """Translates text with engine_name (a translate_engines.ENGINES key),
    resolving its key server-side (D2) rather than accepting one from the
    caller, and saves the result to history -- the same two steps
    tabs/translate_tab.py's own "Translate" button performs. Raises
    InvalidInputError for an unknown engine, UnsupportedOperationError if
    the engine/direction pair is refused (translate_engines.
    standalone_direction_support), and DependencyUnavailableError if the
    engine needs a key/endpoint that isn't configured yet."""
    if engine_name not in translate_engines.ENGINES:
        raise InvalidInputError(f"Unknown translate engine {engine_name!r}.")

    ok, message = translate_engines.standalone_direction_support(
        engine_name, source_language, target_language)
    if not ok:
        raise UnsupportedOperationError(message)

    api_key = _resolve_api_key(engine_name, env_path)
    if api_key is None and engine_name != "nllb":
        raise DependencyUnavailableError(
            f"No {engine_name} key is configured. Set one in Settings first.")

    engine = translate_engines.get_engine(
        engine_name, api_key, model,
        free_tier=free_tier if engine_name == "gemini" else False,
        base_url=base_url if engine_name == "ollama" else None)

    try:
        translated_text = translate_engines.standalone_translate(
            text, engine, source_language, target_language)
    except translate_engines.UnsupportedDirectionError as exc:
        raise UnsupportedOperationError(str(exc)) from exc

    db.save_translate_history(source_language, target_language, engine_name, text, translated_text)
    return {"translated_text": translated_text}
