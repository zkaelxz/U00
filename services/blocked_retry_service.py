"""
services/blocked_retry_service.py -- re-translate ONE line that a
translation engine refused on content-moderation grounds (flag
"content_blocked"), usually with a different engine (parity item R10).
Mirrors the Review tab's "Retry this line with <engine>" control in
`tabs/workspace_tab.py` (Step 31 item 5).

Synchronous, like the tab (a spinner, not a background job): one
`engine.translate_batch([zh], ...)` call for one line. The line is addressed
by permanent `Line.id`, the engine is told that id (`context["line_ids"]`),
and the result is looked up by that id, never by position.

Writes (field-scoped only, never a full line sync):
- success: `en`, `flag` and `flag_note` in ONE compare-and-set UPDATE
  (`db.update_line_fields_if`) that only lands if the line's `zh` and `en`
  still hold what was translated -- an edit made during the engine call is
  a ConflictError with nothing written;
- blocked again: only `flag_note` (the engine's own reason, redacted), the
  same way, and the response says so (`blocked: true`) rather than erroring.

Keys are resolved server-side (never accepted or returned). Any engine
error message goes through `translate_engines.redact_secrets`. The tab's
context is kept: only the drama's source language is sent (no glossary or
style guidelines), so this stays a one-line retry, not a new run.

No Streamlit or FastAPI import; plain dicts in and out.
"""
import db
import translate_engines
from services import settings_service, translate_service
from services.review_lines_service import _line_dict
from services.service_errors import (ConflictError, DependencyUnavailableError,
                                      InvalidInputError, NotFoundError, ServiceError,
                                      UnsupportedOperationError)

BLOCKED_FLAG = "content_blocked"
DEFAULT_ENGINE = "ollama"   # the tab's default: local, no cloud moderation
MAX_REASON_CHARS = 300


def _load(drama_id: int, line_id: int):
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    for ln in db.load_line_objects(drama_id):
        if ln.id == line_id:
            return drama, ln
    raise NotFoundError(f"No line with id {line_id} in this drama.")


def _reload(drama_id: int, line_id: int) -> dict:
    return _line_dict(_load(drama_id, line_id)[1])


def _build_engine(engine_name: str, model, gemini_free_tier):
    if engine_name not in translate_engines.ENGINES:
        raise InvalidInputError("Unknown engine.")
    if (gemini_free_tier and engine_name == "gemini"
            and model in translate_engines.GEMINI_FREE_TIER_UNAVAILABLE_MODELS):
        raise UnsupportedOperationError("That model isn't available on Gemini's free tier.")
    api_key = translate_service.resolve_api_key(engine_name)
    if api_key is None and engine_name != "nllb":
        raise DependencyUnavailableError(
            f"No {engine_name} key is configured. Set one in Settings first.")
    try:
        return translate_engines.get_engine(
            engine_name, api_key, model or None,
            free_tier=engine_name == "gemini" and gemini_free_tier,
            base_url=(settings_service.resolve_key("ollama_url") or None)
            if engine_name == "ollama" else None)
    except ImportError:
        raise DependencyUnavailableError(
            f"The {engine_name} engine isn't installed on this PC.") from None
    except Exception as e:
        raise ServiceError("The engine could not be started: "
                           + translate_engines.redact_secrets(str(e))[:MAX_REASON_CHARS]) from None


def retry_blocked_line(drama_id: int, line_id: int, engine_name: str = DEFAULT_ENGINE,
                       model: str = None, gemini_free_tier: bool = None) -> dict:
    """Re-translates one content-blocked line. Returns
    {drama_id, line_id, engine, model, retried, blocked, reason, line}:
    retried=True with the flag cleared, or blocked=True with the engine's
    (redacted) reason stored as the line's flag note."""
    gemini_free_tier = settings_service.resolve_gemini_free_tier(gemini_free_tier)
    drama, line = _load(drama_id, line_id)
    if line.flag != BLOCKED_FLAG:
        raise ConflictError("This line isn't flagged as blocked by a content filter.")
    if not (line.zh or "").strip():
        raise UnsupportedOperationError("This line has no source text to translate.")
    engine_name = engine_name or DEFAULT_ENGINE
    engine = _build_engine(engine_name, model, gemini_free_tier)
    used_model = getattr(engine, "model", model)
    context = {"source_language": drama.get("source_language") or "zh", "line_ids": [line.id]}
    try:
        results = engine.translate_batch([line.zh], context)
    except translate_engines.ContentModerationBlocked as blocked:
        reason = translate_engines.redact_secrets(
            f"{blocked.engine}: {blocked.reason}")[:MAX_REASON_CHARS]
        if not db.update_line_fields_if(drama_id, line.id, {"flag_note": reason},
                                        {"zh": line.zh, "en": line.en}):
            raise ConflictError("This line changed while retrying; nothing was written.")
        return {"drama_id": drama_id, "line_id": line.id, "engine": engine_name,
                "model": used_model, "retried": False, "blocked": True, "reason": reason,
                "line": _reload(drama_id, line.id)}
    except Exception as e:  # network/engine failure: never leak a key
        raise ServiceError("The engine call failed: "
                           + translate_engines.redact_secrets(str(e))[:MAX_REASON_CHARS]) from None

    # translate_batch answers in the order of the ids it was given; with one
    # id sent, anything but exactly one answer is refused, not guessed at.
    results = list(results or [])
    by_id = dict(zip([line.id], results)) if len(results) == 1 else {}
    translation = by_id.get(line.id)
    if not isinstance(translation, str) or not translation.strip():
        raise ServiceError("The engine returned no translation for this line. Nothing was "
                           "changed; try again or pick another engine.")
    if not db.update_line_fields_if(drama_id, line.id,
                                    {"en": translation, "flag": None, "flag_note": ""},
                                    {"zh": line.zh, "en": line.en}):
        raise ConflictError("This line changed while retrying; nothing was written.")
    return {"drama_id": drama_id, "line_id": line.id, "engine": engine_name,
            "model": used_model, "retried": True, "blocked": False, "reason": None,
            "line": _reload(drama_id, line.id)}
