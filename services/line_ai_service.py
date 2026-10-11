"""
services/line_ai_service.py -- the two per-line AI
helpers from the Review line popover, "Improve translation" and
"Why this?". Plain dicts in and out.

Both are synchronous single-line LLM calls (not background jobs) and NEVER
write: improve returns a suggestion and the
caller applies it through the field-scoped compare-and-set line
patch. The line is looked up by permanent `Line.id`; the text sent to the
model is the line's stored zh/en, not client-supplied text. Keys are
resolved server-side (never accepted or returned), and any engine failure
message is passed through `translate_engines.redact_secrets`. The prompts
are `line_tools.improve_line` / `explain_translation`, reused unchanged;
the drama's glossary, style guidelines and character gender hints are fed
in as the translate run does.
Not supported by the helpers, so not applied: the English-variant locale.

Also here (review parity R17/R18): "Alternatives" (other valid renderings,
each with what it prioritizes) and "Grammar" (a word-by-word breakdown of
the source), `line_tools.alternative_translations` / `grammar_breakdown`,
read-only in the same way. Their results are model output shaped into
bounded lists of short strings; nothing from them is ever written. They
refuse a paid engine once the monthly spending cap is used up.
"""
import core
import db
import line_tools
import translate_engines
from services import (engine_routing_service, settings_service, translate_run_service,
                      translate_service, workspace_job_service)
from services.service_errors import (
    InvalidInputError,
    MissingKeyError,
    NotFoundError,
    ServiceError,
    UnsupportedOperationError,
)

MAX_ISSUE_CHARS = 500


def _drama_tool_engine(drama: dict) -> str:
    """The drama's own translation engine when it can follow instructions;
    otherwise (none saved, a translation-only engine, or one that was removed) the
    engine Settings picks for line helpers (capability
    "llm.instructions"). Configuration only: never a switch on failure."""
    own = drama.get("translation_engine")
    if (own in translate_engines.ENGINES
            and own not in translate_engines.TRANSLATION_ONLY_ENGINES):
        return own
    return engine_routing_service.resolve_capability("llm.instructions")


def tool_engine_name(drama_id: int, engine_name: str = None) -> str:
    """The engine an LLM tool on this drama will use: the named one, else
    _drama_tool_engine. For the router's engines.paid gate, which passes
    this name on, so the call can't switch to an engine the gate didn't see."""
    if engine_name:
        return engine_name
    return _drama_tool_engine(translate_run_service.require_drama(drama_id))


def refuse_if_over_monthly_cap(engine_name: str, gemini_free_tier: bool) -> None:
    """Refuses a paid call once this month's spending cap is used up, as the
    review jobs do (a single short call can't be stopped part way, so there
    is no per-call cap; only the used-up refusal applies)."""
    if not translate_run_service.engine_cap_applies(engine_name, gemini_free_tier):
        return
    monthly = translate_run_service.month_cap_usd()
    if not monthly:
        return
    _, refusal = translate_engines.resolve_cost_cap(None, monthly, db.get_month_spend())
    if refusal:
        raise UnsupportedOperationError(refusal)


def engine_for(drama: dict, engine_name, model, gemini_free_tier, check_cap: bool = False):
    """(engine, engine_name) for an LLM tool on this drama. Keys are resolved
    here, never accepted from the caller. `check_cap` refuses a paid engine
    once the monthly spending cap is used up."""
    gemini_free_tier = settings_service.resolve_gemini_free_tier(gemini_free_tier)
    engine_name = engine_name or _drama_tool_engine(drama)
    if engine_name not in translate_engines.ENGINES:
        raise InvalidInputError(translate_engines.unknown_engine_message(engine_name))
    if engine_name in translate_engines.TRANSLATION_ONLY_ENGINES:
        raise UnsupportedOperationError(
            f"{engine_name} is a translation-only engine and can't do this.")
    if (gemini_free_tier and engine_name == "gemini"
            and model in translate_engines.GEMINI_FREE_TIER_UNAVAILABLE_MODELS):
        raise UnsupportedOperationError("That model isn't available on Gemini's free tier.")
    if check_cap:
        refuse_if_over_monthly_cap(engine_name, gemini_free_tier)
    api_key = translate_service.resolve_api_key(engine_name)
    if api_key is None:
        raise MissingKeyError(engine_name)
    engine = translate_engines.get_engine(
        engine_name, api_key, model,
        free_tier=engine_name == "gemini" and gemini_free_tier,
        base_url=(settings_service.resolve_key("ollama_url") or None)
        if engine_name == "ollama" else None)
    if not getattr(engine, "supports_reference", False):
        raise UnsupportedOperationError(
            f"{engine_name} can't do this; use an LLM engine (Claude, DeepSeek, Ollama...).")
    return engine, engine_name


def _prepare(drama_id: int, line_id: int, engine_name, model, gemini_free_tier,
             need_en: bool = True, check_cap: bool = False):
    drama = translate_run_service.require_drama(drama_id)
    line = next((ln for ln in core.lines_from_rows(db.load_lines(drama_id))
                 if ln.id == line_id), None)
    if line is None:
        raise NotFoundError(f"No line with id {line_id} in this title.")
    if not (line.zh or "").strip():
        raise UnsupportedOperationError("This line has no source text.")
    if need_en and not (line.en or "").strip():
        raise UnsupportedOperationError("This line needs both source text and a translation.")
    engine, engine_name = engine_for(drama, engine_name, model, gemini_free_tier, check_cap)
    return drama, line, engine, engine_name


def _line_language(drama: dict, line) -> str:
    """The language this line is spoken in: its own, else the title's."""
    return line.lang or drama.get("source_language") or "zh"


def run(fn):
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
    preset = "novel" if drama.get("content_mode") == "novel_narration" else "audio_drama"
    # Same builder as a translate run, so the line's emotion guidance
    # is included too.
    _glossary, guidelines, _names = workspace_job_service.build_run_style_context(
        drama_id, drama, [line], preset)
    suggestion = run(lambda: line_tools.improve_line(
        line.zh, line.en, engine, issue=issue,
        source_language=_line_language(drama, line),
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
    text = run(lambda: line_tools.explain_translation(
        line.zh, line.en, engine, source_language=_line_language(drama, line),
        glossary_terms=glossary))
    text = (text or "").strip() if isinstance(text, str) else ""
    if not text:
        raise ServiceError("The engine returned no explanation. Try again.")
    return {"line_id": line.id, "explanation": text, "engine": name,
            "model": getattr(engine, "model", model)}


MAX_ALTERNATIVES = 6
MAX_GRAMMAR_PARTS = 80
MAX_ITEM_CHARS = 1000


def _clean_rows(rows, keys, limit: int) -> list:
    """Model output -> at most `limit` dicts of `keys`, each a short
    string (anything else, e.g. a nested object, is dropped or blanked)."""
    out = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        item = {k: (row.get(k) if isinstance(row.get(k), str) else "").strip()[:MAX_ITEM_CHARS]
                for k in keys}
        if item[keys[0]]:
            out.append(item)
        if len(out) >= limit:
            break
    return out


def alternatives_for_line(drama_id: int, line_id: int, engine_name: str = None,
                          model: str = None, gemini_free_tier: bool = None) -> dict:
    """Other valid translations of one line, each with its approach and what
    it trades away. Writes nothing. Returns
    {line_id, current_en, alternatives: [{translation, approach, tradeoff}],
    engine, model}; apply one through the compare-and-set line patch."""
    drama, line, engine, name = _prepare(drama_id, line_id, engine_name, model,
                                         gemini_free_tier, check_cap=True)
    rows = run(lambda: line_tools.alternative_translations(
        line.zh, line.en, engine, source_language=_line_language(drama, line)))
    alts = _clean_rows(rows, ("translation", "approach", "tradeoff"),
                       MAX_ALTERNATIVES)
    if not alts:
        raise ServiceError("The engine returned no alternatives. Try again.")
    return {"line_id": line.id, "current_en": line.en, "alternatives": alts,
            "engine": name, "model": getattr(engine, "model", model)}


def grammar_for_line(drama_id: int, line_id: int, engine_name: str = None,
                     model: str = None, gemini_free_tier: bool = None) -> dict:
    """A word-by-word breakdown of one line's source text (for learning).
    Needs no translation. Writes nothing. Returns
    {line_id, zh, parts: [{word, reading, meaning, function}], engine, model}."""
    drama, line, engine, name = _prepare(drama_id, line_id, engine_name, model,
                                         gemini_free_tier, need_en=False, check_cap=True)
    rows = run(lambda: line_tools.grammar_breakdown(
        line.zh, engine, source_language=_line_language(drama, line)))
    parts = _clean_rows(rows, ("word", "reading", "meaning", "function"),
                        MAX_GRAMMAR_PARTS)
    if not parts:
        raise ServiceError("The engine returned no breakdown. Try again.")
    return {"line_id": line.id, "zh": line.zh, "parts": parts,
            "engine": name, "model": getattr(engine, "model", model)}
