"""
services/line_ai_service.py -- Migration Slice 50: the two per-line AI
helpers from the Review tab's line popover, "Improve translation" and
"Why this?". Streamlit-free; plain dicts in and out.

Both are synchronous single-line LLM calls (like the tab's spinner calls,
not background jobs) and NEVER write: improve returns a suggestion and the
caller applies it through the Slice 43 field-scoped compare-and-set line
patch. The line is looked up by permanent `Line.id`; the text sent to the
model is the line's stored zh/en, not client-supplied text. Keys are
resolved server-side (never accepted or returned), and any engine failure
message is passed through `translate_engines.redact_secrets`. The prompts
are `line_tools.improve_line` / `explain_translation`, reused unchanged;
the drama's glossary, style guidelines and character gender hints are fed
in as the translate run does (the tab passes only the source language).
Not supported by the helpers, so not applied: the English-variant locale.
"""
import core
import db
import adaptive_style
import line_tools
import translate_engines
import translation_guide
from services import settings_service, translate_run_service, translate_service
from services.service_errors import (DependencyUnavailableError, InvalidInputError,
                                      NotFoundError, ServiceError,
                                      UnsupportedOperationError)

MAX_ISSUE_CHARS = 500


def _prepare(drama_id: int, line_id: int, engine_name, model, gemini_free_tier):
    gemini_free_tier = settings_service.resolve_gemini_free_tier(gemini_free_tier)
    drama = translate_run_service._require_drama(drama_id)
    line = next((ln for ln in core.lines_from_rows(db.load_lines(drama_id))
                 if ln.id == line_id), None)
    if line is None:
        raise NotFoundError(f"No line with id {line_id} in this drama.")
    if not (line.zh or "").strip() or not (line.en or "").strip():
        raise UnsupportedOperationError("This line needs both source text and a translation.")
    engine_name = engine_name or drama.get("translation_engine") or "claude"
    if engine_name not in translate_engines.ENGINES:
        raise InvalidInputError("Unknown engine.")
    if engine_name in translate_engines.TRANSLATION_ONLY_ENGINES:
        raise UnsupportedOperationError(
            f"{engine_name} is a translation-only engine and can't do this.")
    if (gemini_free_tier and engine_name == "gemini"
            and model in translate_engines.GEMINI_FREE_TIER_UNAVAILABLE_MODELS):
        raise UnsupportedOperationError("That model isn't available on Gemini's free tier.")
    api_key = translate_service.resolve_api_key(engine_name)
    if api_key is None and engine_name != "nllb":
        raise DependencyUnavailableError(
            f"No {engine_name} key is configured. Set one in Settings first.")
    engine = translate_engines.get_engine(
        engine_name, api_key, model,
        free_tier=engine_name == "gemini" and gemini_free_tier,
        base_url=(settings_service.resolve_key("ollama_url") or None)
        if engine_name == "ollama" else None)
    if not getattr(engine, "supports_reference", False):
        raise UnsupportedOperationError(
            f"{engine_name} can't do this; use an LLM engine (Claude, DeepSeek, Ollama...).")
    return drama, line, engine, engine_name


def _run(fn):
    try:
        return fn()
    except ServiceError:
        raise
    except Exception as e:  # engine/network failure: never leak a key
        raise ServiceError("The engine call failed: "
                           + translate_engines.redact_secrets(str(e))[:300]) from None


def improve_line(drama_id: int, line_id: int, engine_name: str = None, model: str = None,
                 gemini_free_tier: bool = None, issue: str = "") -> dict:
    """A rewritten translation for one line. Writes nothing.
    Returns {line_id, current_en, suggestion, changed, engine, model}."""
    issue = (issue or "").strip()
    if len(issue) > MAX_ISSUE_CHARS:
        raise InvalidInputError(f"issue is too long (max {MAX_ISSUE_CHARS} characters).")
    drama, line, engine, name = _prepare(drama_id, line_id, engine_name, model,
                                         gemini_free_tier)
    series_id = drama.get("series_id")
    glossary = db.list_glossary_terms(series_id) if series_id else None
    prof = db.get_style_profile(f"series:{series_id}" if series_id else "global")
    learned = adaptive_style.profile_to_prompt_block(prof.get("profile", {})) if prof else ""
    hints = translation_guide.build_character_gender_hints(
        db.list_series_characters(series_id) if series_id else [],
        db.list_characters_with_series_names(drama_id))
    preset = "novel" if drama.get("content_mode") == "novel_narration" else "audio_drama"
    guidelines = translation_guide.build_style_guidelines(
        preset, glossary_terms=glossary,
        custom_notes="\n\n".join(b for b in (learned, hints) if b))
    suggestion = _run(lambda: line_tools.improve_line(
        line.zh, line.en, engine, issue=issue,
        source_language=drama.get("source_language") or "zh",
        style_guidelines=guidelines))
    return {"line_id": line.id, "current_en": line.en, "suggestion": suggestion,
            "changed": suggestion != line.en, "engine": name,
            "model": getattr(engine, "model", model)}


def explain_line(drama_id: int, line_id: int, engine_name: str = None, model: str = None,
                 gemini_free_tier: bool = None) -> dict:
    """Why the line reads the way it does. Writes nothing.
    Returns {line_id, explanation, engine, model}."""
    drama, line, engine, name = _prepare(drama_id, line_id, engine_name, model,
                                         gemini_free_tier)
    series_id = drama.get("series_id")
    glossary = db.list_glossary_terms(series_id) if series_id else None
    text = _run(lambda: line_tools.explain_translation(
        line.zh, line.en, engine, source_language=drama.get("source_language") or "zh",
        glossary_terms=glossary))
    text = (text or "").strip() if isinstance(text, str) else ""
    if not text:
        raise ServiceError("The engine returned no explanation. Try again.")
    return {"line_id": line.id, "explanation": text, "engine": name,
            "model": getattr(engine, "model", model)}
