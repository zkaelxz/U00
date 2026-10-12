"""One click to redo the lines the glossary check flagged, with the stronger engine.

The translate run flags a line "glossary_missing" when its English still lacks
a glossary name after one re-ask on the same engine (engine_backends/
glossary_check.py). This module counts those lines for the Translate stage and
starts the normal run over just them with the engine picked in Settings
(capability "translation.high_quality"), so the monthly cap, the per-run cap,
the usage log and the pre-run undo snapshot all apply as for any run.
"""
import db
from engine_backends.glossary_check import GLOSSARY_FLAG
from services import settings_service, stronger_engine_service, translate_run_service
from services.service_errors import UnsupportedOperationError


def _flagged_rows(drama_id: int) -> list:
    return [r for r in db.load_lines(drama_id)
            if r.get("flag") == GLOSSARY_FLAG and (r.get("zh") or "").strip()
            and (r.get("en") or "").strip()]


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
    on an engine nobody checked. A line edited since the count was read keeps
    its edit."""
    name = engine_name(drama_id)
    if name != engine:
        raise UnsupportedOperationError("The stronger engine changed in Settings. Try again.")
    rows = _flagged_rows(drama_id)
    if not rows:
        raise UnsupportedOperationError("No lines are flagged for a missing glossary name.")
    started = translate_run_service.start_translate_run(
        drama_id, engine_name=name, force_retranslate=True, line_ids=[r["id"] for r in rows],
        own_lines_only=True, expected_en={r["id"]: r["en"] for r in rows},
        locale=settings_service.get("default_locale"),
        style_note=settings_service.get("default_style_note") or "",
        allow_paid_summary=allow_paid_summary, thinking=thinking)
    return {**started, "line_ids": started.get("line_ids", [r["id"] for r in rows])}
