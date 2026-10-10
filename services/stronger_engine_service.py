"""
services/stronger_engine_service.py -- tiered translation
cost/quality. A drama is translated with its everyday (often cheap or local)
engine; on a line that looks hard, Review SUGGESTS the stronger engine the
user picked in Settings (capability "translation.high_quality") for
that one line. User decision 2026-09-29: suggest only, never switch
automatically -- nothing here runs on its own or writes a line.

- get_suggestions(): which lines to suggest it for, and why:
  "qc_flag" (the line carries a review flag), "glossary_conflict" (a glossary
  term is in the source but its translation is not in the English) or
  "ambiguous_term" (the review flag is an ambiguous reference or an uncertain
  name). Plus the per-line cost estimate (whole prompt included). No engine
  call. Offered only once the user picked a stronger engine in Settings.
- try_line(): one single-line translate with the stronger engine, with the
  same glossary, style guidelines, character hints, speaker name and novel
  reference a translate run sends, plus the lines before it.
  Returns the text for the user to accept (the client applies it through
  the compare-and-set line patch) -- it never writes the line.
  Refused once the monthly cap is used up or when the estimate would pass
  it; the spend is logged against the drama, also when the call fails or
  its answer is rejected (it may still have been billed).

The line is addressed by permanent `Line.id` and the engine is told that id;
the answer is taken only when exactly one comes back (never by position).
Keys are resolved server-side, never accepted or returned; an engine failure
is a fixed message with the redacted detail in the app log.
"""
import re

import core
import db
import translate_engines
from engine_backends import llm_tasks
from services import (engine_routing_service, settings_service, translate_run_service,
                      translate_service, workspace_job_service)
from services.service_errors import (
    ConflictError,
    DependencyUnavailableError,
    MissingKeyError,
    NotFoundError,
    ServiceError,
    UnsupportedOperationError,
)

CAPABILITY = "translation.high_quality"
OPERATION = "stronger_line"
CONTEXT_LINES = 3
MAX_LOG_CHARS = 300
_CJK_RE = re.compile(r"[぀-ヿ㐀-鿿가-힯]")
REASONS = {
    "qc_flag": "Flagged in review",
    "glossary_conflict": "A glossary term isn't used",
    "ambiguous_term": "Flagged as an ambiguous name or reference",
}
# The review flags that mean "ambiguous": an unresolved pronoun/reference, or
# a name that may be misspelled or inconsistently romanized.
AMBIGUOUS_FLAGS = {"ambiguous_reference", "name_uncertain"}


def _drama(drama_id: int) -> dict:
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No title with id {drama_id}.")
    return drama


def _glossary(drama: dict) -> list:
    series_id = drama.get("series_id")
    return db.list_glossary_terms(series_id) if series_id else []


def _current_engine(drama: dict) -> str:
    return (drama.get("translation_engine")
            or engine_routing_service.resolve_capability("translation.cheap"))


def _stronger(drama: dict):
    """The engine to suggest, or None: only one the user picked explicitly in
    Settings (the unset default could be weaker than the drama's own engine)
    and not the one the drama already uses."""
    if not engine_routing_service.is_configured(CAPABILITY):
        return None
    stronger = engine_routing_service.resolve_capability(CAPABILITY)
    return stronger if stronger != _current_engine(drama) else None


def _targets(term: dict) -> list:
    aliases = [a.strip() for a in re.split(r"[|,，、]", term.get("aliases") or "") if a.strip()]
    trans = (term.get("term_translation") or "").strip()
    return [t for t in [trans] + [a for a in aliases if not _CJK_RE.search(a)] if t]


def line_reasons(line, glossary_terms) -> list:
    """Why a stronger engine may help this line ([] = no suggestion)."""
    zh, en = (line.zh or "").strip(), (line.en or "").strip()
    if not zh or not en:
        return []
    reasons = []
    if line.flag in AMBIGUOUS_FLAGS:
        reasons.append("ambiguous_term")
    elif line.flag and line.flag != "content_blocked":  # blocked lines have their own retry
        reasons.append("qc_flag")
    matched = translate_engines.matching_glossary_terms(zh, glossary_terms)
    en_low = en.lower()
    if any(_targets(t) and not any(x.lower() in en_low for x in _targets(t)) for t in matched):
        reasons.append("glossary_conflict")
    return reasons


class _Probe:
    """Stands in for an engine where only name/model/free_tier/
    supports_reference are read (estimates, the prompt context)."""
    def __init__(self, engine_name: str):
        self.name = engine_name
        self.model = _default_model(engine_name)
        self.free_tier = engine_name == "gemini" and settings_service.get_gemini_free_tier()
        self.supports_reference = engine_name not in translate_engines.TRANSLATION_ONLY_ENGINES


def _default_model(engine_name: str):
    return translate_engines.effective_default_model(engine_name)


def _run_context(drama_id: int, drama: dict, lines: list, engine) -> tuple:
    """(context, character_names): the same glossary, style guidelines
    (learned style, character gender hints), locale and novel reference a
    translate run sends (workspace_job_service.build_run_style_context)."""
    is_novel = drama.get("content_mode") == "novel_narration"
    glossary, style, character_names = workspace_job_service.build_run_style_context(
        drama_id, drama, lines, "novel" if is_novel else "audio_drama", with_emotions=False)
    context = translate_engines.build_translation_context(
        engine, drama, locale=settings_service.get_preference("default_locale"),
        novel_reference=translate_run_service.load_novel_reference(drama_id, drama),
        glossary_terms=glossary, style_guidelines=style,
        ollama_num_ctx_override=settings_service.get_ollama_num_ctx_override() or None)
    return context, character_names


def _prompt_chars(context: dict) -> int:
    try:
        return len(translate_engines.build_stable_system_text(dict(context)))
    except Exception:
        return 0


def _estimate(engine_name: str, zh: str, prompt_chars: int, context_chars: int = 0) -> float:
    """Cost of one single-line call: the whole fixed prompt (instructions,
    glossary, style guide, novel excerpt) plus the line and its context,
    at the same ~3.5 chars/token as the run estimate. No key needed."""
    probe = _Probe(engine_name)
    input_tokens = int((prompt_chars + context_chars + len(zh)) / 3.5) + 100
    output_tokens = int(len(zh) / 2.5) + 20
    return round(translate_engines.estimate_cost_for_engine(probe, input_tokens, output_tokens), 6)


def _recent(lines: list, pos: int) -> list:
    return [(ln.zh, ln.en) for ln in lines[max(0, pos - CONTEXT_LINES):pos] if (ln.en or "").strip()]


def get_suggestions(drama_id: int) -> dict:
    """{drama_id, engine, current_engine, available, reason_labels, lines:
    [{line_id, reasons, estimate_usd}]}. available is False (and lines is
    empty) until a stronger engine is picked in Settings, or when it is the
    one the drama already uses."""
    drama = _drama(drama_id)
    stronger = _stronger(drama)
    current = _current_engine(drama)
    out = {"drama_id": drama_id,
           "engine": stronger or engine_routing_service.resolve_capability(CAPABILITY),
           "current_engine": current, "available": stronger is not None,
           "reason_labels": dict(REASONS), "lines": []}
    if stronger is None:
        return out
    lines = core.lines_from_rows(db.load_lines(drama_id))
    glossary = _glossary(drama)
    prompt_chars = None
    for pos, ln in enumerate(lines):
        reasons = line_reasons(ln, glossary)
        if not reasons:
            continue
        if prompt_chars is None:  # built once, only when something is suggested
            prompt_chars = _prompt_chars(_run_context(drama_id, drama, lines, _Probe(stronger))[0])
        ctx_chars = sum(len(z) + len(e) for z, e in _recent(lines, pos))
        out["lines"].append({"line_id": ln.id, "reasons": reasons,
                             "estimate_usd": _estimate(stronger, ln.zh, prompt_chars, ctx_chars)})
    return out


def stronger_engine_name(drama_id: int) -> str:
    """The engine try_line will call, for the router's engines.paid gate;
    the router passes it on so the call can't use another one."""
    return _stronger(_drama(drama_id)) or engine_routing_service.resolve_capability(CAPABILITY)


def _log_failure(exc: Exception):
    try:
        import applog
        applog.get_logger().error("stronger-engine line: "
                                  + translate_engines.redact_secrets(str(exc))[:MAX_LOG_CHARS])
    except Exception:
        pass


def _refuse_over_cap(engine_name: str, free_tier: bool, estimate: float):
    if not translate_run_service.engine_cap_applies(engine_name, free_tier):
        return
    monthly = translate_run_service.month_cap_usd()
    if not monthly:
        return
    spent = db.get_month_spend()
    _, refusal = translate_engines.resolve_cost_cap(None, monthly, spent)
    if refusal:
        raise UnsupportedOperationError(refusal)
    if spent + estimate > monthly:
        raise UnsupportedOperationError(
            "This would go over your monthly spending cap. Raise the cap in Settings to try it.")


def _log_spend(drama_id: int, engine_name: str, engine) -> float:
    """Logs whatever the engine reports it used (a failed or rejected call
    can still be billed) and returns its cost."""
    usage = getattr(engine, "last_usage", None) or {}
    if not usage:
        return 0.0
    cost = translate_engines.estimate_cost_for_engine(
        engine, usage.get("input_tokens", 0), usage.get("output_tokens", 0),
        usage.get("cache_read_tokens", 0), usage.get("cache_write_tokens", 0))
    db.log_usage(drama_id, engine_name, getattr(engine, "model", "") or "", OPERATION,
                 usage.get("input_tokens", 0), usage.get("output_tokens", 0), cost,
                 usage.get("cache_read_tokens", 0))
    return cost


def try_line(drama_id: int, line_id: int, engine_name: str) -> dict:
    """Translates one line with the stronger engine. `engine_name` is the
    one the router's paid-engine check saw; if Settings changed since, the
    call is refused (409) rather than run on an engine nobody checked.
    Returns {drama_id, line_id, engine, model, text, based_on_en, cost_usd};
    writes nothing to the line (based_on_en is the English it was compared
    against, so the client's compare-and-set patch can refuse a stale
    apply)."""
    drama = _drama(drama_id)
    lines = core.lines_from_rows(db.load_lines(drama_id))
    pos = next((i for i, ln in enumerate(lines) if ln.id == line_id), None)
    if pos is None:
        raise NotFoundError(f"No line with id {line_id} in this title.")
    line = lines[pos]
    if not (line.zh or "").strip():
        raise UnsupportedOperationError("This line has no source text.")
    stronger = _stronger(drama)
    if stronger is None:
        raise UnsupportedOperationError(
            "Pick a stronger engine than this title's own in Settings > Which engine does "
            "what first.")
    if stronger != engine_name:
        raise ConflictError("The stronger engine was changed in Settings; try again.")
    if translate_engines.is_english_line(line):
        # Already English: the "translation" is the line itself, with no call or cost.
        return {"drama_id": drama_id, "line_id": line.id, "engine": engine_name,
                "model": None, "text": line.zh.strip(), "based_on_en": line.en or "",
                "cost_usd": 0.0}
    api_key = translate_service.resolve_api_key(engine_name)
    if api_key is None:
        raise MissingKeyError(engine_name)
    free_tier = engine_name == "gemini" and settings_service.get_gemini_free_tier()
    context, character_names = _run_context(drama_id, drama, lines, _Probe(engine_name))
    recent = _recent(lines, pos)
    _refuse_over_cap(engine_name, free_tier, _estimate(
        engine_name, line.zh, _prompt_chars(context), sum(len(z) + len(e) for z, e in recent)))
    try:
        engine = translate_engines.get_engine(
            engine_name, api_key, None, free_tier=free_tier,
            base_url=(settings_service.resolve_key("ollama_url") or None)
            if engine_name == "ollama" else None)
    except ImportError:
        raise DependencyUnavailableError(
            f"The {engine_name} engine isn't installed on this PC.") from None
    except Exception as e:
        _log_failure(e)
        raise ServiceError("The engine could not be started.") from None

    context["novel_reference"] = (context.get("novel_reference")
                                  if getattr(engine, "supports_reference", False) else None)
    context["line_ids"] = [line.id]
    context["line_languages"] = translate_engines.tagged_line_languages(
        [line], context["source_language"])
    context["speaker_labels"] = [character_names.get(line.speaker)]
    context["recent_context"] = recent
    try:
        try:
            results = list(llm_tasks.call_batch_bounded(
                engine, lambda: engine.translate_batch([line.zh], context)) or [])
        finally:
            cost = _log_spend(drama_id, engine_name, engine)
    except translate_engines.ContentModerationBlocked:
        raise UnsupportedOperationError(
            f"{engine_name} refused this line on content grounds.") from None
    except Exception as e:
        _log_failure(e)
        raise ServiceError("The engine call failed.") from None
    # One id sent: exactly one answer is accepted, never guessed by position.
    text = results[0] if len(results) == 1 else None
    if not isinstance(text, str) or not text.strip():
        raise ServiceError("The engine returned no translation for this line.")
    return {"drama_id": drama_id, "line_id": line.id, "engine": engine_name,
            "model": getattr(engine, "model", None), "text": text.strip(),
            "based_on_en": line.en or "", "cost_usd": round(cost, 6)}
