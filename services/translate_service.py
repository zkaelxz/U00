"""
services/translate_service.py -- metadata and translate action for the
standalone translate tool.
The read-only "list engines" / "list history" half (docs/archive/migration-review.md
section 3.7) is paired with translate() itself, resolving a server-side key per engine (D2 --
docs/archive/migration-review.md section 6) rather than accepting one from the
caller.

clear_history() is the one piece deliberately
added separately from the read-only half and translate().
"""
from typing import Optional

import db
import translate_engines
from services import ownership_service, settings_service
from services.service_errors import (
    DependencyUnavailableError,
    InvalidInputError,
    MissingKeyError,
    UnsupportedOperationError,
)

# translate_engines.ENGINES keys whose engine class needs no API key to run
# (see translate_engines.py's own ENGINES / FREE_ENGINES):
#   - nllb: a locally-downloaded model, no key at all.
#   - ollama: its key is optional, defaulting to the literal "local" when
#     nothing is configured (resolve_api_key below) -- it points
#     at a locally-run server, not a hosted API that requires an account
#     key. There's nothing meaningful to "configure" in the same sense as an
#     API key, so it's reported as configured too.
# Everything else (claude/deepseek/gemini/openai) maps directly onto
# services.settings_service.key_status(), which is keyed by the same engine
# name for these three.
_NO_KEY_REQUIRED_ENGINES = translate_engines.KEYLESS_ENGINES

# Engine name -> the model dict (if any) the user picks a model from for
# that engine.
ENGINE_MODEL_DICTS = {
    "claude": translate_engines.CLAUDE_MODELS,
    "gemini": translate_engines.GEMINI_MODELS,
    "openai": translate_engines.OPENAI_MODELS,
    "ollama": translate_engines.OLLAMA_MODELS,
    "nllb": translate_engines.NLLB_MODELS,
}


def list_engines(env_path: Optional[str] = None) -> list:
    """One entry per translate_engines.ENGINES key: name, display label,
    whether it's free (translate_engines.FREE_ENGINES), its selectable
    model keys (or None if the engine has no model picker), and whether a
    key/endpoint is configured for it (never the key value itself -- see
    services.settings_service.key_status). The Gemini label reflects the
    persisted "Gemini free tier" setting (translate_engines.engine_picker_label)."""
    from services import model_registry_service  # imports this module at load time
    key_status = settings_service.key_status(env_path)
    gemini_free_tier = settings_service.get_gemini_free_tier()
    engines = []
    for name in translate_engines.ENGINES:
        model_dict = ENGINE_MODEL_DICTS.get(name)
        if name in _NO_KEY_REQUIRED_ENGINES:
            key_configured = True
        else:
            key_configured = bool(key_status.get(name, False))
        models = list(model_dict.keys()) if model_dict is not None else None
        extras = model_registry_service.extra_models(name)
        chosen = translate_engines.override_models(name)
        if extras or chosen:
            # DeepSeek has no built-in picker: its built-in default plus the
            # extras and the models the user chose in Diagnostics.
            builtin = translate_engines.builtin_default_model(name)
            base = models if models is not None else ([builtin] if builtin else [])
            models = list(dict.fromkeys(base + extras + chosen))
        engines.append({
            "name": name,
            "label": translate_engines.engine_picker_label(name, gemini_free_tier),
            "free": name in translate_engines.FREE_ENGINES,
            "models": models,
            "model_labels": {m: model_registry_service.extra_model_label(name, m) for m in extras},
            "key_configured": key_configured,
        })
    return engines


def list_history(limit: int = 50, principal=None) -> list:
    """Thin wrapper over db.list_translate_history -- most recent first.
    A household user (auth B2) sees only their own rows."""
    return db.list_translate_history(
        limit=limit, visible_to=ownership_service.visible_to_filter(principal))


def resolve_api_key(engine_name: str, env_path: Optional[str] = None) -> Optional[str]:
    """The literal value to pass into translate_engines.get_engine, per
    engine:
      - nllb: None -- a locally-downloaded model, nothing to pass.
      - ollama: a resolved key/URL if configured, else the literal "local"
        (it points at a locally-run server).
      - everything else: whatever services.settings_service.resolve_key
        finds, or None if nothing is configured.
    """
    if engine_name == "nllb":
        return None
    if engine_name in translate_engines.KEYLESS_ENGINES:
        return settings_service.resolve_key(engine_name, env_path) or "local"
    return settings_service.resolve_key(engine_name, env_path)


def translate(text: str, engine_name: str, source_language: str, target_language: str,
              model: Optional[str] = None, free_tier: Optional[bool] = None,
              env_path: Optional[str] = None, principal=None) -> dict:
    """Translates text with engine_name (a translate_engines.ENGINES key),
    resolving its key server-side (D2) rather than accepting one from the
    caller, and saves the result to history. Raises
    InvalidInputError for an unknown engine, UnsupportedOperationError if
    the engine/direction pair is refused (translate_engines.
    standalone_direction_support), and DependencyUnavailableError if the
    engine needs a key/endpoint that isn't configured yet. free_tier None
    means the saved Gemini free-tier setting. Ollama always uses the
    configured Ollama URL; a caller-supplied URL is never fetched (SSRF)."""
    if engine_name not in translate_engines.ENGINES:
        raise InvalidInputError(translate_engines.unknown_engine_message(engine_name))

    ok, message = translate_engines.standalone_direction_support(
        engine_name, source_language, target_language)
    if not ok:
        raise UnsupportedOperationError(message)

    api_key = resolve_api_key(engine_name, env_path)
    if api_key is None and engine_name != "nllb":
        raise MissingKeyError(engine_name)

    engine = translate_engines.get_engine(
        engine_name, api_key, model,
        free_tier=(settings_service.resolve_gemini_free_tier(free_tier)
                   if engine_name == "gemini" else False),
        base_url=((settings_service.resolve_key("ollama_url") or None)
                  if engine_name == "ollama" else None))

    try:
        translated_text = translate_engines.standalone_translate(
            text, engine, source_language, target_language)
    except translate_engines.UnsupportedDirectionError as exc:
        raise UnsupportedOperationError(str(exc)) from exc
    except translate_engines.OllamaUnavailableError as exc:
        raise DependencyUnavailableError(
            translate_engines.redact_secrets(exc.message), details={"reason": exc.reason}) from None

    db.save_translate_history(source_language, target_language, engine_name, text, translated_text,
                              user_id=None if principal is None else principal.get("user_id"))
    return {"translated_text": translated_text}


def clear_history(confirm: bool = False) -> dict:
    """Deletes every row in the (global, not drama-scoped) translate_history
    table via db.clear_translate_history(). A deliberate two-step gate
    against an accidental permanent delete: confirm must be passed as True,
    or the call is refused with InvalidInputError rather than silently
    no-op'ing or silently proceeding."""
    if confirm is not True:
        raise InvalidInputError(
            "Clearing translate history requires confirm=true. "
            "This permanently deletes all saved translation history.")
    db.clear_translate_history()
    return {"cleared": True}
