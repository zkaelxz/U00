"""
services/pending_install_service.py -- Diagnostics installs that wait for the
next start (see pending_install.py for why).

Preview the plan, queue it, cancel it and report its outcome. The pip step
itself runs in `python -m pending_install` before the server starts. Routes are
PC only (`local_only()`): a household user never sees or cancels a queued
install.
"""

import copy
import logging
import threading
import time

import background_jobs
from jobs import job_launch
import install_plan
import install_registry
import pending_install
from services import diagnostics_admin_service as admin_svc
from lib.errors import ConflictError, InvalidInputError, UnsupportedOperationError
from services import app_update_service, auth_service, source_update_service
from lib.errors import ServiceError

log = logging.getLogger(__name__)
PLAN_CACHE_SECONDS = 600
# Time to press Cancel after confirming, before Baihe restarts itself.
MAX_QUEUED_KEYS = 40     # installer/service_update.MAX_QUEUED_KEYS
AUTO_GRACE_SECONDS = 180
AUTO_RETRY_SECONDS = 3600
OLD_HELPER = ("Baihe runs as a background service here, and its helper is from an older Setup. "
              "Run the new Setup once (Windows asks for administrator approval); after that "
              "queued installs restart Baihe by themselves.")
_LOCK = threading.Lock()
_PREVIEW_LOCK = threading.Lock()     # one pip dry-run at a time
_CACHE = {"keys": None, "plan": None, "at": 0.0}
_auto = {"created": None, "at": 0.0}

_PROBLEMS = {
    "modified": "The queued install file was changed after Baihe wrote it, so it was not run.",
    "invalid": "The queued install file wasn't understood, so it was not run.",
    "unreadable": "The queued install file couldn't be read, so it was not run.",
}


class PlanNeedsConfirm(admin_svc.AdminActionRefused, ConflictError):
    """The plan downgrades something Baihe needs; accept_risk=true is required (409)."""


def require_installable_here() -> None:
    """A queued install is run by this account at start-up, or, for the boot
    service whose install folder is locked, by the BaiheUpdate helper. Without
    either, pip would fail part-way after removing files, so queueing is refused."""
    if service_can_restart():
        return
    from services import diagnostics_installs_service
    diagnostics_installs_service._require_writable_site_packages()


def require_within_cap(keys) -> None:
    """The BaiheUpdate helper (installer/service_update.MAX_QUEUED_KEYS) refuses a longer queue."""
    if len(keys) > MAX_QUEUED_KEYS:
        raise admin_svc.AdminActionNotPossible(
            f"At most {MAX_QUEUED_KEYS} packages can be queued at once; install the rest afterwards.")


def _keys(packages) -> list:
    keys = list(dict.fromkeys(packages))
    reason = install_registry.unqueueable_reason(keys)
    if reason:
        raise admin_svc.AdminActionUnknownPackage(reason)
    return keys


def _jobs_running() -> bool:
    from services import library_admin_service
    return library_admin_service.any_job_running()


def _plan(keys: list, fresh: bool) -> dict:
    with _LOCK:
        if (not fresh and _CACHE["keys"] == sorted(keys)
                and time.monotonic() - _CACHE["at"] < PLAN_CACHE_SECONDS):
            # A job may have imported a package since the preview, so what is
            # in use (and so install-now vs restart) is asked again.
            return install_plan.apply_in_use(copy.deepcopy(_CACHE["plan"]), _jobs_running())
    plan = install_plan.build_plan(keys, jobs_running=_jobs_running())
    with _LOCK:
        _CACHE.update(keys=sorted(keys), plan=plan, at=time.monotonic())
    return plan


def _view(plan: dict) -> dict:
    return {"packages": plan["keys"], "available": plan["available"], "mode": plan["mode"],
            "changes": [{"name": c["name"], "from_version": c["from"], "to_version": c["to"],
                         "kind": c["kind"]} for c in plan["changes"]],
            "summary": plan["summary"], "loaded": plan["loaded"], "blocked": plan["blocked"],
            "needs_confirm": plan["needs_confirm"], "note": plan["note"]}


def preview(packages) -> dict:
    keys = _keys(packages)
    # The dry-run reads the installed set and the package index; during an
    # install (or a restore) that answer would be wrong and the extra pip a burden.
    if job_launch.exclusive_held() or not _PREVIEW_LOCK.acquire(blocking=False):
        raise ConflictError("An install, restore or another preview is in progress; "
                            "try again in a moment.")
    try:
        if job_launch.exclusive_held():
            raise ConflictError("An install, restore or another preview is in progress; "
                                "try again in a moment.")
        return _view(_plan(keys, fresh=True))
    finally:
        _PREVIEW_LOCK.release()


def queue(packages, confirm: bool = False, accept_risk: bool = False) -> dict:
    """Queues the install when it must wait for a restart. When it is safe to
    run now this only says so (install_now) and the caller uses the normal
    install; a plan with a hard block, or a risk not accepted, is refused."""
    if confirm is not True:
        raise admin_svc.AdminActionUnconfirmed("Confirmation required.")
    # A merged queue runs a fresh dry run, so it takes the preview's lock and re-check.
    if job_launch.exclusive_held() or not _PREVIEW_LOCK.acquire(blocking=False):
        raise ConflictError("An install, restore or another preview is in progress; "
                            "try again in a moment.")
    try:
        if job_launch.exclusive_held():
            raise ConflictError("An install, restore or another preview is in progress; "
                                "try again in a moment.")
        return _queue(packages, accept_risk)
    finally:
        _PREVIEW_LOCK.release()


def _queue(packages, accept_risk: bool) -> dict:
    require_installable_here()
    current, _problem = pending_install.read_pending()
    queued = (current or {}).get("packages", [])
    # The queue runs as one pip command, so it is planned (clash check, risks)
    # as a whole: a package that is fine alone can clash with one already queued.
    keys = _keys(queued + list(packages))
    require_within_cap(keys)
    plan = _plan(keys, fresh=bool(queued))
    if plan["blocked"]:
        raise admin_svc.AdminActionNotPossible(" ".join(plan["blocked"]))
    if plan["needs_confirm"] and accept_risk is not True:
        raise PlanNeedsConfirm(" ".join(plan["needs_confirm"])
                               + " Confirm again to go ahead anyway.")
    if plan["mode"] != "restart" and not queued:
        return {"queued": False, "install_now": True, "plan": _view(plan)}
    before = {**(current or {}).get("before", {}), **plan["before"]}
    pending_install.write_pending(keys, before)
    return {"queued": True, "install_now": False, "plan": _view(plan)}


def cancel() -> dict:
    if pending_install.apply_running():
        raise ConflictError("The install is running right now and can't be cancelled.")
    return {"cancelled": pending_install.cancel()}


def dismiss_result() -> dict:
    pending_install.clear_result()
    return {"dismissed": True}


def service_can_restart() -> bool:
    """The boot service's BaiheUpdate helper can restart the app for a queued install."""
    return (source_update_service.runs_as_service() and app_update_service.updater_present()
            and app_update_service.updater_restarts_for_packages())


def _restart_problem():
    if source_update_service.runs_as_service():
        return None if service_can_restart() else OLD_HELPER
    return source_update_service.restart_problem()


def restart_info() -> dict:
    """Whether Baihe can restart itself to run the queued install, and the jobs
    that would be stopped by it."""
    jobs = background_jobs.active_job_ids()
    problem = _restart_problem()
    return {"available": problem is None,
            "reason": problem,
            "automatic": problem is None and service_can_restart(),
            "running_jobs": jobs,
            "service": source_update_service.runs_as_service(),
            # Another Baihe process's job can't be listed or stopped from here.
            "other_jobs": _jobs_running() and not jobs}


def restart_and_apply(confirm: bool, stop_jobs: bool, actor_id=None) -> dict:
    """Restarts Baihe so the queued install runs before anything is loaded.
    Never stops a running job unless the owner said so (stop_jobs); a job in
    another process is never stopped."""
    if confirm is not True:
        raise InvalidInputError("Confirm to restart Baihe and install the queued packages.")
    if pending_install.read_pending()[0] is None:
        raise ConflictError("Nothing is waiting to be installed.")
    if pending_install.apply_running():
        raise ConflictError("An install is already running.")
    problem = _restart_problem()
    if problem:
        raise UnsupportedOperationError(problem)
    info = restart_info()
    if info["other_jobs"]:
        raise ConflictError("Another running Baihe process has a job. Let it finish first.")
    if info["running_jobs"] and stop_jobs is not True:
        raise ConflictError("Jobs are running: " + ", ".join(info["running_jobs"])
                            + ". Restarting stops them; confirm that too, or wait for them to finish.")
    if service_can_restart():
        # The service has no running job to lose here, or the owner chose to stop them.
        app_update_service.request_package_restart()
        auth_service.write_audit(actor_id, "app.restart", "queued package install")
    else:
        source_update_service.restart_through_start_bat(actor_id, "queued package install")
    return {"restarting": True, "stopped_jobs": info["running_jobs"] if stop_jobs else []}


def auto_apply_tick(now=None) -> None:
    """From the background poller: a queued install the owner confirmed is
    applied by restarting the service once nothing is running. Waits for jobs
    and Live sessions (both are jobs), an install or restore in progress, and
    the cancel grace; one attempt per queued install per hour. Never raises."""
    now = time.time() if now is None else now
    try:
        if not service_can_restart():
            return
        settle_after_helper()
        payload, _problem = pending_install.read_pending()
        if payload is None or pending_install.apply_running():
            return
        if now - payload["created"] < AUTO_GRACE_SECONDS:
            return
        if _auto["created"] == payload["created"] and now - _auto["at"] < AUTO_RETRY_SECONDS:
            return
        if job_launch.exclusive_held() or background_jobs.active_job_ids() or _jobs_running():
            return
        _auto.update(created=payload["created"], at=now)
        app_update_service.request_package_restart()
        auth_service.write_audit(None, "app.restart", "queued package install (automatic)")
    except ServiceError as exc:
        log.warning("automatic package install restart waits: %s", type(exc).__name__)
    except Exception as exc:
        log.warning("automatic package install restart failed: %s", type(exc).__name__)


def settle_after_helper() -> None:
    """The helper ran this queued install: its files in the data folder are the
    app's to remove, since the helper never deletes there. This is also what stops
    the automatic restart from asking again for the same install."""
    payload, _problem = pending_install.read_pending()
    last = app_update_service.read_package_restart_result()
    if pending_install.apply_running():
        return
    if payload is None:
        app_update_service.remove_package_restart_request()
    elif last and last.get("queued_created") == payload["created"]:
        pending_install.cancel()
        app_update_service.remove_package_restart_request()


def get_status() -> dict:
    settle_after_helper()
    state = pending_install.status()
    pending = state["pending"] or {}
    return {"packages": pending.get("packages", []), "created": pending.get("created"),
            "before": pending.get("before", {}),
            "problem": _PROBLEMS.get(state["problem"]),
            "result": state["result"], "applying": state["applying"], "restart": restart_info(),
            "last_restart": app_update_service.read_package_restart_result()}
