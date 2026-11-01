"""
services/translate_run_options.py -- the request checks of start_translate_run:
which options may combine, and which lines a run will translate.
"""

import bulk_translate
import db
from engine_backends import local, engine_registry
from engine_backends import fallback as engine_fallback
from services import ownership_service, paid_job_service, settings_service
from lib.errors import (
    ConflictError,
    InvalidInputError,
    UnsupportedOperationError,
)


def ollama_reachable() -> bool:
    """Parity X24: whether the configured Ollama server answers (the
    "Can't reach Ollama" warning). A boolean only; the URL never leaves
    the server. Cached briefly by engine_backends."""
    return bool(local.check_ollama_reachable(
        settings_service.ollama_url()))


def check_mode_options(engine_name, gemini_free_tier, reflect, bulk, fallback_chain,
                        own_lines_only, line_ids) -> None:
    if fallback_chain and (reflect or bulk):
        raise InvalidInputError("A fallback chain only applies to a normal translation run.")
    if own_lines_only and line_ids is None:
        raise InvalidInputError("own_lines_only needs line_ids.")
    if bulk and line_ids is not None:
        raise InvalidInputError("Bulk mode translates the whole title; line_ids isn't supported.")
    if reflect and engine_name in engine_registry.TRANSLATION_ONLY_ENGINES:
        raise UnsupportedOperationError(f"{engine_name} can't run Reflect mode.")
    if bulk and (engine_name not in bulk_translate.BULK_ENGINES
                 or (engine_name == "gemini" and gemini_free_tier)):
        raise UnsupportedOperationError("Bulk mode needs Claude, Gemini (paid) or DeepSeek.")
    if bulk and reflect and engine_name == "deepseek":
        raise UnsupportedOperationError("Bulk Reflect needs Claude or Gemini's batch API.")


BULK_IN_FLIGHT_STATUSES = db.BULK_PENDING_STATUSES + ("submitting", "running", "applying")


def select_lines(drama_id, line_ids, expected_en, force_retranslate):
    """(all lines, target ids or None, lines this run will translate)."""
    lines = db.load_line_objects(drama_id)
    if not lines:
        raise UnsupportedOperationError("This title has no lines to translate yet.")
    target_ids = None
    if line_ids is not None:
        target_ids = set(line_ids)
        if not target_ids or not target_ids <= {ln.id for ln in lines}:
            raise InvalidInputError("line_ids must be a non-empty list of this title's line ids.")
        if expected_en is not None:
            target_ids = {ln.id for ln in lines if ln.id in target_ids
                          and (ln.en or "") == expected_en.get(ln.id)}
            if not target_ids:
                raise ConflictError("The chosen lines were edited since they were picked. "
                                    "Pick them again.", details={"reason": "stale_preview"})
    eligible = [ln for ln in lines if (ln.zh or "").strip()
                and (force_retranslate or not (ln.en or "").strip())
                and (target_ids is None or ln.id in target_ids)]
    if not eligible:
        raise UnsupportedOperationError("There are no lines to translate.")
    if force_retranslate and any((ln.en or "").strip() for ln in eligible):
        ownership_service.require_acting_lines_edit()
    return lines, target_ids, eligible


def instantiate_engines(built, chain, caps):
    """(engines, the engine the run uses, its cost cap). With a fallback chain the
    run uses a FallbackEngine over all of them."""
    engines = [engine_registry.get_engine(
        name, key, mdl, free_tier=free_tier,
        base_url=settings_service.ollama_url()
        if name == "ollama" else None) for name, key, mdl, free_tier in built]
    if len(engines) > 1:
        engine = engine_fallback.FallbackEngine(engines, [c["engine"] for c in chain], caps)
        return engines, engine, None  # each engine's own cap is enforced by the FallbackEngine
    return engines, engines[0], caps[0]


def require_no_pending_bulk_overlap(drama_id, eligible) -> None:
    """A live run over lines a paid bulk batch is still translating would turn
    the batch into a charge for nothing: its results are discarded for every
    line whose English changed since submission."""
    ours = {ln.id for ln in eligible}
    for job in db.list_bulk_jobs(drama_id, statuses=BULK_IN_FLIGHT_STATUSES):
        if (job.get("kind") or "translate") != "translate":
            continue
        if ours & {r["line_id"] for r in db.list_bulk_job_lines(job["id"])}:
            raise ConflictError(
                "A bulk translation is still pending for some of these lines. "
                "Wait for it to finish, or cancel it first.")


def require_bulk_ready(drama_id, engine_name, model, main_cap, reflect, force_retranslate,
                       gemini_free_tier, job_cost_cap_usd, estimate_cost) -> None:
    # A batch or an off-peak run is paid for with nobody watching.
    paid_job_service.require_cap_for_unattended(engine_name, gemini_free_tier)
    if db.list_bulk_jobs(drama_id, statuses=db.BULK_PENDING_STATUSES + ("running",)):
        raise ConflictError("A bulk translation is already pending for this title.")
    if engine_name != "deepseek" and main_cap is not None:
        # DeepSeek off-peak runs as a normal run later and stops at the
        # cap; a submitted batch can't, so it's refused up front.
        if estimate_cost(
                drama_id, engine_name, model, reflect=reflect, bulk=True,
                force_retranslate=force_retranslate, gemini_free_tier=gemini_free_tier,
                job_cost_cap_usd=job_cost_cap_usd)["estimate_above_cap"]:
            raise UnsupportedOperationError(
                "Not submitted: a bulk batch can't be stopped part-way, and its estimate "
                "is above your cap. Raise the cap, or run a normal translation.")
