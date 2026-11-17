"""Money limits for a background job whose AI calls are not translation batches
(review passes, glossary, narration, Scanlate, Fix flagged...).

job_calls gives every call_llm_json in the block a deadline, a cancel that
works mid-call, the monthly cap re-checked before each request, an end after a
run of failed requests, and one rate-limit wait budget. The reason a rule ended
the job goes in the job's result as "stopped".

Bulk batches are billed only when their results land, so each one holds its
estimate of the monthly cap until then (hold_bulk_spend)."""
import contextlib
import threading

import background_jobs
import bulk_providers
import db
from engine_backends import llm_tasks, paid_calls
from lib.errors import ConflictError, UnsupportedOperationError
import paid_limits

NO_CAP_MESSAGE = bulk_providers.NO_CAP_MESSAGE


def _cap_spent(engine_name: str, engine):
    """A callable that is true once this month's cap is used up, or None for
    an engine the cap does not cover."""
    # Imported here: translate_run_service imports the job runners that use this module.
    from services import translate_run_service
    free_tier = bool(getattr(engine, "free_tier", False))
    model = getattr(engine, "model", None)
    if not translate_run_service.engine_cap_applies(engine_name, free_tier, model):
        return None

    def spent() -> bool:
        try:
            translate_run_service.refuse_when_cap_spent(engine_name, free_tier, model)
        except UnsupportedOperationError:
            return True
        return False
    return spent


paid_calls.set_default_cap_check_factory(_cap_spent)


def _stop_job_over_member_limit(user_id, job_id) -> None:
    """A job whose paid call just used up its starter's own monthly limit is
    asked to stop; a translation keeps the lines it finished, as on Cancel."""
    if job_id and paid_limits.paid_limit_reached(user_id):
        background_jobs.request_cancel(job_id)


db.on_member_spend(_stop_job_over_member_limit)


def require_cap_for_unattended(engine_name: str, gemini_free_tier: bool, model) -> None:
    """Refuses paid work started with nobody watching (a batch submit, an
    off-peak run, a bulk series) while no monthly cap is set."""
    from services import translate_run_service
    if (translate_run_service.engine_cap_applies(engine_name, gemini_free_tier, model)
            and translate_run_service.month_cap_usd() <= 0):
        raise UnsupportedOperationError(NO_CAP_MESSAGE)


def require_monthly_cap(engine_models) -> float:
    """The monthly cap, for a job that spends with nobody watching: refused
    when it uses a capped engine and no cap is set. engine_models: (engine
    name, model) pairs."""
    from services import settings_service
    free_tier = bool(settings_service.get("gemini_free_tier"))
    for name, model in set(engine_models):
        require_cap_for_unattended(name, free_tier, model)
    return settings_service.get_monthly_cap_usd()


# Bulk batches accepted in this process whose row may not exist yet, by
# (drama id, family). A batch is billed only when its results land hours
# later, so until then its estimate holds part of the monthly cap; without
# this, two titles' batches each pass against the same room.
_bulk_holds: dict = {}
_bulk_holds_lock = threading.Lock()


def bulk_hold_key(drama_id: int, kind: str) -> tuple:
    """A title's translate and Reflect batches share one job, so one key."""
    return (drama_id, "translate" if kind in ("translate", "reflect") else kind)


def _held_usd(exclude=None, member: int | None = None) -> float:
    # Under _bulk_holds_lock. A row whose key is still held here is the same
    # batch as the hold, so it isn't counted twice. member: only that
    # member's batches (None: everyone's).
    total = sum(usd for key, (usd, user) in _bulk_holds.items()
                if key != exclude and member in (None, user))
    for drama_id, kind, _pipeline, usd, user in db.list_unbilled_bulk_estimates():
        key = bulk_hold_key(drama_id, kind)
        if key not in _bulk_holds and key != exclude and member in (None, user):
            total += usd
    return total


def _over_room(estimate_usd: float, limit: float, logged: float, held: float, whose: str) -> None:
    room = limit - logged - held
    if estimate_usd > room:
        raise UnsupportedOperationError(
            f"Not submitted: this batch's estimate (${estimate_usd:.2f}) is more than the "
            f"${max(room, 0.0):.2f} left of {whose} once other pending bulk jobs "
            f"(${held:.2f}) are counted. Wait for them to finish, or raise the limit.")


def hold_bulk_spend(key: tuple, estimate_usd: float) -> None:
    """Holds estimate_usd of this month's cap, and of the member's own limit
    when they have one, for a batch about to be sent; refuses it when the cap
    or limit less spend and every other unbilled batch leaves less than that.
    Release with release_bulk_spend once the batch's row (which carries the
    estimate) exists, or the job ends."""
    monthly = _monthly_cap()
    user_id = db.current_spender()
    member_limit = paid_limits.paid_limit_usd(user_id)
    with _bulk_holds_lock:
        if key in _bulk_holds:
            # Releasing a refused twin's hold would drop this one's.
            raise ConflictError("A bulk job for this title is already being sent.")
        if monthly > 0:
            _over_room(estimate_usd, monthly, db.get_logged_month_spend(),
                       _held_usd(exclude=key), "this month's cap")
        if member_limit is not None and user_id is not None:
            _over_room(estimate_usd, member_limit, db.get_logged_user_month_spend(user_id),
                       _held_usd(exclude=key, member=user_id), "your own monthly limit")
        _bulk_holds[key] = (float(estimate_usd), user_id)


def release_bulk_spend(key: tuple) -> None:
    with _bulk_holds_lock:
        _bulk_holds.pop(key, None)


def holding_bulk_spend(key: tuple, fn):
    """fn, releasing the hold under key when it returns or raises: wrap both
    the submit (the row now carries the estimate) and the job's runner (a job
    cancelled before it submits never calls the submit)."""
    def run(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        finally:
            release_bulk_spend(key)
    return run


def _monthly_cap() -> float:
    from services import settings_service
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
