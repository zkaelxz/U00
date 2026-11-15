"""One click to redo the lines the glossary check flagged, with the stronger engine.

The translate run flags a line "glossary_missing" when its English still lacks
a glossary name after one re-ask on the same engine (engine_backends/
glossary_check.py). This module counts those lines for the Translate stage and
starts the normal run over just them with the engine picked in Settings
(capability "translation.high_quality"), so the monthly cap, the per-run cap,
the usage log and the pre-run undo snapshot all apply as for any run.

The same redo is offered for "doubtful" lines: the ones whose English reads unlike
its source (source_mismatch) and the ones the AI flagged as uncertain, for which
the monthly cap and the estimate shown before the click work the same way.
"""
import logging

import db
from engine_backends.alignment_check import ALIGNMENT_FLAG
from engine_backends.flag_reasons import FLAG_REASONS
from engine_backends.glossary_check import GLOSSARY_FLAG, missing_terms
from services import ownership_service, profile_service, settings_service, stronger_engine_service, translate_run_service
from lib.errors import InvalidInputError, ServiceError, UnsupportedOperationError

log = logging.getLogger(__name__)

KIND_GLOSSARY = "glossary"
KIND_DOUBTFUL = "doubtful"
# FLAG_REASONS are the reasons the translating AI may give; the glossary flag has its own redo.
DOUBTFUL_FLAGS = frozenset({ALIGNMENT_FLAG, *FLAG_REASONS})


def _flagged_rows(drama_id: int) -> list:
    """Flagged lines whose CURRENT English still lacks a glossary name: a line the
    person fixed by hand keeps its flag but is no longer a miss."""
    flagged = [r for r in db.load_line_texts(drama_id)
               if r.flag == GLOSSARY_FLAG and (r.zh or "").strip() and (r.en or "").strip()]
    if not flagged:
        return []
    drama = db.get_drama(drama_id) or {}
    terms = db.list_run_glossary_terms(drama)
    return [r._asdict() for r in flagged if missing_terms(r.zh, r.en, terms)]


def _doubtful_rows(drama_id: int) -> list:
    """Flagged lines nobody has settled: Save edits clears the flag, but find-and-replace
    and memory Accept leave it, and Check marks the line reviewed, so reviewed lines
    are left out rather than paid for again."""
    return [r._asdict() for r in db.load_line_texts(drama_id)
            if r.flag in DOUBTFUL_FLAGS and not r.reviewed
            and (r.zh or "").strip() and (r.en or "").strip()]


def doubtful_config_fields(drama_id: int) -> dict:
    """Count of doubtful lines and what redoing them would cost, so the click is an
    informed one. The estimate is None for a free engine or when it can't be made."""
    rows = _doubtful_rows(drama_id)
    estimate = None
    engine = stronger_engine_service.stronger_engine_or_none(drama_id) if rows else None
    if engine:
        try:
            estimate = translate_run_service.estimate_translate_cost(
                drama_id, engine_name=engine, force_retranslate=True,
                line_ids={r["id"] for r in rows})
            estimate = None if estimate["free"] else estimate["estimated_usd"]
        except ServiceError:
            # A missing key or an offline engine is reported when the redo starts.
            log.info("No cost estimate for the doubtful-lines redo of title %s", drama_id, exc_info=True)
    return {"doubtful_flagged_count": len(rows), "doubtful_redo_estimate_usd": estimate}


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


def _rows_for_ids(drama_id: int, line_ids: list) -> list:
    wanted = set(line_ids)
    rows = [r for r in db.load_line_texts(drama_id)
            if r.id in wanted and (r.zh or "").strip() and (r.en or "").strip()]
    if len(rows) != len(wanted):
        raise InvalidInputError("line_ids must be translated lines of this title.")
    return [r._asdict() for r in rows]


def start_redo(drama_id: int, engine: str, allow_paid_summary: bool = True,
               thinking: bool | None = None, line_ids: list | None = None,
               kind: str = KIND_GLOSSARY) -> dict:
    """Runs the stronger engine over the flagged lines. `engine` is the name the
    caller was checked for; a Settings change since is refused rather than run
    on an engine nobody checked. Lines that no longer miss a name are left out;
    a line edited between reading it here and the run's own read is dropped
    (expected_en), so the run never overwrites English it did not see.
    `line_ids` (the series consistency report's per-flag redo) replaces the flagged
    set with those lines of this title. `kind` "doubtful" redoes the lines flagged
    source_mismatch or uncertain instead of the glossary ones, and replaces those flags."""
    doubtful = kind == KIND_DOUBTFUL
    if doubtful and line_ids:
        raise InvalidInputError("line_ids can't be combined with the doubtful lines.")
    name = engine_name(drama_id)
    if name != engine:
        raise UnsupportedOperationError("The stronger engine changed in Settings. Try again.")
    if doubtful:
        rows = _doubtful_rows(drama_id)
    else:
        rows = _rows_for_ids(drama_id, line_ids) if line_ids else _flagged_rows(drama_id)
    if not rows:
        raise UnsupportedOperationError(
            "No lines are flagged as doubtful." if doubtful
            else "No lines are flagged for a missing glossary name.")
    locale, style_note = profile_service.locale_and_note(
        ownership_service.require_drama(drama_id), str(settings_service.get("default_locale")),
        str(settings_service.get("default_style_note") or ""))
    started = translate_run_service.start_translate_run(
        drama_id, engine_name=name, force_retranslate=True, line_ids=[r["id"] for r in rows],
        own_lines_only=True, expected_en={r["id"]: r["en"] for r in rows},
        locale=locale, style_note=style_note,
        allow_paid_summary=allow_paid_summary, thinking=thinking,
        replace_flags=DOUBTFUL_FLAGS if doubtful else frozenset())
    return {**started, "line_ids": started.get("line_ids", [r["id"] for r in rows])}
