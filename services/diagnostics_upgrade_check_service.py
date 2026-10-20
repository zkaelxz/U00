"""
services/diagnostics_upgrade_check_service.py -- the Diagnostics
"Test first" action for an update, run as a background job:

Q06 "Test first": upgrade_check.check_upgrade_candidate for one package's
update target (a throwaway environment plus this app's test suite, so
minutes, not seconds). The version comes only from the last "Check for
updates" (the same cached target the Upgrade route installs). An installed
copy has no tests/ folder, so the status says Test first is unavailable.

It starts through the install guard of diagnostics_admin_service and
shares the installs' state lock (diagnostics_installs_service).
"""

import background_jobs
import diagnostics_torch
import upgrade_check
from jobs import job_launch
from services import diagnostics_admin_service as admin_svc
from services import diagnostics_gaps_service as gaps
from services import diagnostics_packages_service as packages_svc
from diagnostics_report import redact_for_support
from services.diagnostics_installs_service import _OUTPUT_TAIL, _STATE_LOCK, _job_view

UPGRADE_CHECK_JOB_ID = "upgrade_check"
_UPGRADE_CHECK = {"package": None, "target": None, "tail": [], "last": None}


def get_upgrade_check() -> dict:
    with _STATE_LOCK:
        state = {"package": _UPGRADE_CHECK["package"], "target": _UPGRADE_CHECK["target"],
                 "output_tail": list(_UPGRADE_CHECK["tail"]),
                 "result": dict(_UPGRADE_CHECK["last"]) if _UPGRADE_CHECK["last"] else None}
    unavailable = (None if upgrade_check.test_suite_available(gaps.default_project_root())
                   else upgrade_check.NO_TEST_SUITE)
    return {**state, "unavailable_reason": unavailable,
            "job_id": UPGRADE_CHECK_JOB_ID, "job": _job_view(UPGRADE_CHECK_JOB_ID)}


def start_upgrade_check(name: str, target: str = None, confirm: bool = False) -> dict:
    """PC only. `target` must be the last "Check for updates" target for
    `name` (409 otherwise, as for Upgrade); the job installs exactly that
    version (with constraints.txt) into a throwaway environment and runs
    this app's tests against it. Your real install is not touched."""
    if name in diagnostics_torch.TORCH_FAMILY:
        admin_svc.guard(confirm)
        raise admin_svc.AdminActionNotPossible(
            "torch, torchvision and torchaudio are set up together under GPU PyTorch.")
    admin_svc.guard(confirm)
    if name not in packages_svc.installable_packages():
        raise admin_svc.AdminActionUnknownPackage("Unknown or non-installable package.")
    if not upgrade_check.test_suite_available(gaps.default_project_root()):
        raise admin_svc.AdminActionNotPossible(upgrade_check.NO_TEST_SUITE)
    checked = packages_svc.cached_update(name)
    if checked is None or checked.get("status") != "update" or not checked.get("target"):
        raise admin_svc.AdminActionStale("Check for updates first; there is no update to test.")
    if target != checked["target"]:
        raise admin_svc.AdminActionStale("The update check has changed since; check for updates again.")
    dist, version = checked["dist"], checked["target"]
    # Started and labelled under one lock: the state names a package only
    # once its job really started, and the job's own writes (tail, result)
    # wait for this lock, so a second request can't relabel the first run.
    with _STATE_LOCK:
        if not job_launch.start_job(UPGRADE_CHECK_JOB_ID, _upgrade_check_job, name, dist,
                                         version, description=f"Testing {name} {version}",
                                         line_fields=()):
            raise admin_svc.AdminActionJobsRunning(
                "An update test or a library restore is already running; try again when it ends.")
        _UPGRADE_CHECK.update(package=name, target=version, tail=[], last=None)
    return {"job_id": UPGRADE_CHECK_JOB_ID, "started": True}


_PHASES = (("Creating a throwaway", 0.05), ("Installing ", 0.15),
           ("Running this app's test suite", 0.35), ("test(s) failed -- re-running", 0.8))
_RESULT_KEYS = ("ok", "verdict", "reason", "version", "new_failures",
                "preexisting_failures", "conflicts")


def _upgrade_check_job(name: str, dist: str, version: str):
    def cancelled():
        return background_jobs.is_cancel_requested(UPGRADE_CHECK_JOB_ID)
    # The runner polls `cancelled`, so Cancel kills a quiet pip/pytest tree too.
    gen = upgrade_check.check_upgrade_candidate(dist, version, project_root=gaps.default_project_root(),
                                              cancel=cancelled)
    frac, final = 0.0, None
    try:
        for item in gen:
            if cancelled():
                gen.close()          # kills the running pip/pytest
                raise background_jobs.JobCancelled()
            if item.get("done"):
                final = item
                continue
            line = redact_for_support(item.get("line", ""))[:300]
            for marker, value in _PHASES:
                if marker in line:
                    frac = max(frac, value)
            with _STATE_LOCK:
                _UPGRADE_CHECK["tail"] = (_UPGRADE_CHECK["tail"] + [line])[-_OUTPUT_TAIL:]
            if line.strip():
                background_jobs.update_progress(UPGRADE_CHECK_JOB_ID, frac, line[:200])
    finally:
        gen.close()
    if cancelled():
        raise background_jobs.JobCancelled()     # the generator ended early because of Cancel
    final = final or {"ok": False, "verdict": "incomplete", "reason": "the test run stopped"}
    result = {}
    for k in _RESULT_KEYS:
        v = final.get(k)
        if isinstance(v, list):
            v = [redact_for_support(x)[:300] for x in v[:50]]
        elif isinstance(v, str):
            v = redact_for_support(v)[:500]
        result[k] = v
    result["ok"] = bool(result["ok"])
    with _STATE_LOCK:
        _UPGRADE_CHECK["last"] = result
    background_jobs.set_result(UPGRADE_CHECK_JOB_ID, {"status": result["verdict"],
                                                      "detail": result["reason"]})
