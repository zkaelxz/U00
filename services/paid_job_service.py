"""Money limits for a background job whose AI calls are not translation batches
(review passes, glossary, narration, Scanlate, Fix flagged...).

job_calls gives every call_llm_json in the block a deadline, a cancel that
works mid-call, the monthly cap re-checked before each request, an end after a
run of failed requests, and one rate-limit wait budget. The reason a rule ended
the job goes in the job's result as "stopped"."""
import contextlib

import background_jobs
import bulk_providers
from engine_backends import llm_tasks, paid_calls
from lib.errors import UnsupportedOperationError

NO_CAP_MESSAGE = bulk_providers.NO_CAP_MESSAGE


def _cap_spent(engine_name: str, engine):
    """A callable that is true once this month's cap is used up, or None for
    an engine the cap does not cover."""
    # Imported here: translate_run_service imports the job runners that use this module.
    from services import translate_run_service
    free_tier = bool(getattr(engine, "free_tier", False))
    if not translate_run_service.engine_cap_applies(engine_name, free_tier):
        return None

    def spent() -> bool:
        try:
            translate_run_service.refuse_when_cap_spent(engine_name, free_tier)
        except UnsupportedOperationError:
            return True
        return False
    return spent


paid_calls.set_default_cap_check_factory(_cap_spent)


def require_cap_for_unattended(engine_name: str, gemini_free_tier: bool = False) -> None:
    """Refuses paid work started with nobody watching (a batch submit, an
    off-peak run, a bulk series) while no monthly cap is set."""
    from services import translate_run_service
    if (translate_run_service.engine_cap_applies(engine_name, gemini_free_tier)
            and translate_run_service.month_cap_usd() <= 0):
        raise UnsupportedOperationError(NO_CAP_MESSAGE)


def require_monthly_cap(engine_names) -> float:
    """The monthly cap, for a job that spends with nobody watching: refused
    when it uses a capped engine and no cap is set."""
    from services import settings_service
    free_tier = bool(settings_service.get("gemini_free_tier"))
    for name in set(engine_names):
        require_cap_for_unattended(name, free_tier)
    return settings_service.get_monthly_cap_usd()


@contextlib.contextmanager
def job_calls(job_id: str, drama_id, engine, engine_name: str, operation: str, *,
              log_usage: bool = False, lenient_empty: bool = True, on_wait=None,
              no_thinking: bool = False, deadline: float | None = None):
    """Yields the CallLimits; read stop_reason from it, or stop_fields, once
    the job's calls are done. log_usage: write each call's usage row here, for
    a job whose callers pass no usage_cb of their own."""
    with llm_tasks.bounded_job_calls(
            job_id, lambda: background_jobs.is_cancel_requested(job_id), deadline=deadline,
            lenient_empty=lenient_empty, on_wait=on_wait, no_thinking=no_thinking):
        with paid_calls.call_limits(engine, engine_name, operation, drama_id=drama_id,
                                    cap_spent=_cap_spent(engine_name, engine),
                                    log_usage=log_usage) as limits:
            yield limits


def stop_fields(limits) -> dict:
    return {"stopped": limits.stop_reason} if limits.stop_reason else {}
