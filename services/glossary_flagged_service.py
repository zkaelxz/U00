"""One click to redo the lines the glossary check flagged, with the stronger engine.

The translate run flags a line "glossary_missing" when its English still lacks
a glossary name after one re-ask on the same engine (engine_backends/
glossary_check.py). This module counts those lines for the Translate stage and
starts the normal run over just them with the engine picked in Settings
(capability "translation.high_quality"), so the monthly cap, the per-run cap,
the usage log and the pre-run undo snapshot all apply as for any run.
"""
import db
from engine_backends.glossary_check import GLOSSARY_FLAG, missing_terms
from services import profile_service, settings_service, stronger_engine_service, translate_run_service
from lib.errors import UnsupportedOperationError


def _flagged_rows(drama_id: int) -> list:
    """Flagged lines whose CURRENT English still lacks a glossary name: a line the
    person fixed by hand keeps its flag but is no longer a miss."""
    flagged = [r for r in db.load_line_texts(drama_id)
               if r.flag == GLOSSARY_FLAG and (r.zh or "").strip() and (r.en or "").strip()]
    if not flagged:
        return []
    drama = db.get_drama(drama_id) or {}
    series_id = drama.get("series_id")
    terms = db.list_glossary_terms(series_id) if series_id else []
    return [r._asdict() for r in flagged if missing_terms(r.zh, r.en, terms)]


def config_fields(drama_id: int) -> dict:
    return {"glossary_flagged_count": len(_flagged_rows(drama_id)),
            "glossary_redo_engine": stronger_engine_service.stronger_engine_or_none(drama_id)}


def engine_name(drama_id: int) -> str:
    """The engine the redo will use, for the route's engines.paid check."""
    name = stronger_engine_service.stronger_engine_or_none(drama_id)
    if name is None:
        raise UnsupportedOperationError(
            "Pick a stronger engine in Settings (different from this title's engine) first.")
    return name


def start_redo(drama_id: int, engine: str, allow_paid_summary: bool = True,
               thinking: bool = None) -> dict:
    """Runs the stronger engine over the flagged lines. `engine` is the name the
    caller was checked for; a Settings change since is refused rather than run
    on an engine nobody checked. Lines that no longer miss a name are left out;
    a line edited between reading it here and the run's own read is dropped
    (expected_en), so the run never overwrites English it did not see."""
    name = engine_name(drama_id)
    if name != engine:
        raise UnsupportedOperationError("The stronger engine changed in Settings. Try again.")
    rows = _flagged_rows(drama_id)
    if not rows:
        raise UnsupportedOperationError("No lines are flagged for a missing glossary name.")
    locale, style_note = profile_service.locale_and_note(
        db.get_drama(drama_id), settings_service.get("default_locale"),
        settings_service.get("default_style_note") or "")
    started = translate_run_service.start_translate_run(
        drama_id, engine_name=name, force_retranslate=True, line_ids=[r["id"] for r in rows],
        own_lines_only=True, expected_en={r["id"]: r["en"] for r in rows},
        locale=locale, style_note=style_note,
        allow_paid_summary=allow_paid_summary, thinking=thinking)
    return {**started, "line_ids": started.get("line_ids", [r["id"] for r in rows])}
