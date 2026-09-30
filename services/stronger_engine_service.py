"""
services/stronger_engine_service.py -- Step 99: tiered translation
cost/quality. A drama is translated with its everyday (often cheap or local)
engine; on a line that looks hard, Review SUGGESTS the stronger engine the
user picked in Settings (Step 36 capability "translation.high_quality") for
that one line. User decision 2026-09-29: suggest only, never switch
automatically -- nothing here runs on its own or writes a line.

- get_suggestions(): which lines to suggest it for, and why:
  "qc_flag" (the line carries a review flag), "glossary_conflict" (a glossary
  term is in the source but its translation is not in the English) or
  "ambiguous_term" (a source term in the line has two or more different
  glossary translations). Plus the per-line cost estimate. No engine call.
- try_line(): one single-line translate with the stronger engine, with the
  drama's glossary and style guidelines and the lines around it as context.
  Returns the text for the user to accept (the client applies it through
  the Slice 43 compare-and-set line patch) -- it never writes the line.
  Refused once the monthly cap is used up or when the estimate would pass
  it; the spend is logged against the drama.

The line is addressed by permanent `Line.id` and the engine is told that id;
the answer is taken only when exactly one comes back (never by position).
Keys are resolved server-side, never accepted or returned; an engine failure
is a fixed message with the redacted detail in the app log.
"""
import inspect
import re

import core
import db
import translate_engines
import translation_guide
from services import engine_routing_service, settings_service, translate_run_service, translate_service
from services.service_errors import (DependencyUnavailableError, NotFoundError, ServiceError,
                                      UnsupportedOperationError)

CAPABILITY = "translation.high_quality"
OPERATION = "stronger_line"
CONTEXT_LINES = 3
MAX_LOG_CHARS = 300
_CJK_RE = re.compile(r"[぀-ヿ㐀-鿿가-힯]")
REASONS = {
    "qc_flag": "Flagged in review",
    "glossary_conflict": "A glossary term isn't used",
    "ambiguous_term": "A term has more than one glossary translation",
}


def _drama(drama_id: int) -> dict:
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    return drama


def _glossary(drama: dict) -> list:
    series_id = drama.get("series_id")
    return db.list_glossary_terms(series_id) if series_id else []


def _current_engine(drama: dict) -> str:
    return (drama.get("translation_engine")
            or engine_routing_service.resolve_capability("translation.cheap"))


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
    if line.flag and line.flag != "content_blocked":  # blocked lines have their own retry
        reasons.append("qc_flag")
    matched = translate_engines.matching_glossary_terms(zh, glossary_terms)
    en_low = en.lower()
    if any(_targets(t) and not any(x.lower() in en_low for x in _targets(t)) for t in matched):
        reasons.append("glossary_conflict")
    by_source = {}
    for t in glossary_terms or []:
        src = (t.get("term_original") or "").strip()
        trans = (t.get("term_translation") or "").strip().lower()
        if src and trans:
            by_source.setdefault(src, set()).add(trans)
    if any(len(by_source.get((t.get("term_original") or "").strip(), ())) > 1 for t in matched):
        reasons.append("ambiguous_term")
    return reasons


def _estimate(engine_name: str, zh: str) -> float:
    """Cost estimate for one line (prompt overhead included), without a key."""
    class _Probe:  # estimate_cost_for_engine reads only name/model/free_tier
        name = engine_name
        model = _default_model(engine_name)
        free_tier = engine_name == "gemini" and settings_service.get_gemini_free_tier()
    return round(translate_engines.estimate_translation_cost(_Probe(), [zh]), 6)


def _default_model(engine_name: str):
    cls = translate_engines.ENGINES.get(engine_name)
    try:
        default = inspect.signature(cls.__init__).parameters.get("model")
        return default.default if default and default.default is not inspect._empty else None
    except (TypeError, ValueError):
        return None


def get_suggestions(drama_id: int) -> dict:
    """{drama_id, engine, current_engine, available, reason_labels, lines:
    [{line_id, reasons, estimate_usd}]}. available is False (and lines is
    empty) when the stronger engine is the one the drama already uses."""
    drama = _drama(drama_id)
    stronger = engine_routing_service.resolve_capability(CAPABILITY)
    current = _current_engine(drama)
    out = {"drama_id": drama_id, "engine": stronger, "current_engine": current,
           "available": stronger != current, "reason_labels": dict(REASONS), "lines": []}
    if not out["available"]:
        return out
    glossary = _glossary(drama)
    for ln in core.lines_from_rows(db.load_lines(drama_id)):
        reasons = line_reasons(ln, glossary)
        if reasons:
            out["lines"].append({"line_id": ln.id, "reasons": reasons,
                                 "estimate_usd": _estimate(stronger, ln.zh)})
    return out


def stronger_engine_name(drama_id: int) -> str:
    """The engine try_line will call, for the router's engines.paid gate."""
    _drama(drama_id)
    return engine_routing_service.resolve_capability(CAPABILITY)


def _log_failure(exc: Exception):
    try:
        import applog
        applog.get_logger().error("stronger-engine line: "
                                  + translate_engines.redact_secrets(str(exc))[:MAX_LOG_CHARS])
    except Exception:
        pass


def _refuse_over_cap(engine_name: str, free_tier: bool, estimate: float):
    if not translate_run_service._cap_applies(engine_name, free_tier):
        return
    monthly = translate_run_service._monthly_cap()
    if not monthly:
        return
    spent = db.get_month_spend()
    _, refusal = translate_engines.resolve_cost_cap(None, monthly, spent)
    if refusal:
        raise UnsupportedOperationError(refusal)
    if spent + estimate > monthly:
        raise UnsupportedOperationError(
            "This would go over your monthly spending cap. Raise the cap in Settings to try it.")


def try_line(drama_id: int, line_id: int) -> dict:
    """Translates one line with the stronger engine. Returns {drama_id,
    line_id, engine, model, text, based_on_en, cost_usd}; writes nothing to
    the line (based_on_en is the English it was compared against, so the
    client's compare-and-set patch can refuse a stale apply)."""
    drama = _drama(drama_id)
    lines = core.lines_from_rows(db.load_lines(drama_id))
    pos = next((i for i, ln in enumerate(lines) if ln.id == line_id), None)
    if pos is None:
        raise NotFoundError(f"No line with id {line_id} in this drama.")
    line = lines[pos]
    if not (line.zh or "").strip():
        raise UnsupportedOperationError("This line has no source text.")
    engine_name = engine_routing_service.resolve_capability(CAPABILITY)
    if engine_name == _current_engine(drama):
        raise UnsupportedOperationError(
            "The stronger engine is the one this drama already uses. Pick another one in "
            "Settings > Which engine does what.")
    api_key = translate_service.resolve_api_key(engine_name)
    if api_key is None and engine_name != "nllb":
        raise DependencyUnavailableError(
            f"No {engine_name} key is configured. Set one in Settings first.")
    free_tier = engine_name == "gemini" and settings_service.get_gemini_free_tier()
    _refuse_over_cap(engine_name, free_tier, _estimate(engine_name, line.zh))
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

    glossary = _glossary(drama)
    is_novel = drama.get("content_mode") == "novel_narration"
    style = translation_guide.build_style_guidelines(
        "novel" if is_novel else "audio_drama", glossary_terms=glossary or None)
    context = translate_engines.build_translation_context(
        engine, drama, locale=settings_service.get_preference("default_locale"),
        glossary_terms=glossary or None, style_guidelines=style)
    context["line_ids"] = [line.id]
    context["recent_context"] = [(ln.zh, ln.en) for ln in lines[max(0, pos - CONTEXT_LINES):pos]
                                 if (ln.en or "").strip()]
    try:
        results = list(engine.translate_batch([line.zh], context) or [])
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
    usage = getattr(engine, "last_usage", None) or {}
    cost = translate_engines.estimate_cost_for_engine(
        engine, usage.get("input_tokens", 0), usage.get("output_tokens", 0),
        usage.get("cache_read_tokens", 0), usage.get("cache_write_tokens", 0))
    if usage:
        db.log_usage(drama_id, engine_name, getattr(engine, "model", "") or "", OPERATION,
                     usage.get("input_tokens", 0), usage.get("output_tokens", 0), cost,
                     usage.get("cache_read_tokens", 0))
    return {"drama_id": drama_id, "line_id": line.id, "engine": engine_name,
            "model": getattr(engine, "model", None), "text": text.strip(),
            "based_on_en": line.en or "", "cost_usd": round(cost, 6)}
