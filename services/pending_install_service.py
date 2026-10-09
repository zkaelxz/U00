"""
services/pending_install_service.py -- Diagnostics installs that wait for the
next start (see pending_install.py for why).

Preview the plan, queue it, cancel it and report its outcome. The pip step
itself runs in `python -m pending_install` before the server starts. Routes are
PC only (`local_only()`): a household user never sees or cancels a queued
install.
"""

import threading
import time

import install_plan
import install_registry
import pending_install
from services import diagnostics_gaps_service as gaps
from services.service_errors import ConflictError, InvalidInputError

PLAN_CACHE_SECONDS = 600
_LOCK = threading.Lock()
_CACHE = {"keys": None, "plan": None, "at": 0.0}

_PROBLEMS = {
    "modified": "The queued install file was changed after Baihe wrote it, so it was not run.",
    "invalid": "The queued install file wasn't understood, so it was not run.",
    "unreadable": "The queued install file couldn't be read, so it was not run.",
}


class PlanNeedsConfirm(gaps.AdminActionRefused, ConflictError):
    """The plan downgrades something Baihe needs; accept_risk=true is required (409)."""


def _keys(packages) -> list:
    keys = list(dict.fromkeys(packages))
    reason = install_registry.unqueueable_reason(keys)
    if reason:
        raise gaps.AdminActionUnknownPackage(reason)
    return keys


def _jobs_running() -> bool:
    from services import library_admin_service
    return library_admin_service.any_job_running()


def _plan(keys: list, fresh: bool) -> dict:
    with _LOCK:
        if (not fresh and _CACHE["keys"] == sorted(keys)
                and time.monotonic() - _CACHE["at"] < PLAN_CACHE_SECONDS):
            return _CACHE["plan"]
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
    return _view(_plan(_keys(packages), fresh=True))


def queue(packages, confirm: bool = False, accept_risk: bool = False) -> dict:
    """Queues the install when it must wait for a restart. When it is safe to
    run now this only says so (install_now) and the caller uses the normal
    install; a plan with a hard block, or a risk not accepted, is refused."""
    if confirm is not True:
        raise gaps.AdminActionUnconfirmed("Confirmation required.")
    keys = _keys(packages)
    plan = _plan(keys, fresh=False)
    if plan["blocked"]:
        raise gaps.AdminActionNotPossible(plan["blocked"][0])
    if plan["needs_confirm"] and accept_risk is not True:
        raise PlanNeedsConfirm(" ".join(plan["needs_confirm"])
                               + " Confirm again to go ahead anyway.")
    if plan["mode"] != "restart":
        return {"queued": False, "install_now": True, "plan": _view(plan)}
    current, _problem = pending_install.read_pending()
    merged = list(dict.fromkeys((current or {}).get("packages", []) + keys))
    before = {**(current or {}).get("before", {}), **plan["before"]}
    pending_install.write_pending(merged, before)
    return {"queued": True, "install_now": False, "plan": _view(plan)}


def cancel() -> dict:
    if pending_install.apply_running():
        raise ConflictError("The install is running right now and can't be cancelled.")
    return {"cancelled": pending_install.cancel()}


def dismiss_result() -> dict:
    pending_install.clear_result()
    return {"dismissed": True}


def get_status() -> dict:
    state = pending_install.status()
    pending = state["pending"] or {}
    return {"packages": pending.get("packages", []), "created": pending.get("created"),
            "before": pending.get("before", {}),
            "problem": _PROBLEMS.get(state["problem"]),
            "result": state["result"], "applying": state["applying"]}
