"""
services/diagnostics_admin_service.py -- the shared refusals and guard for
the Diagnostics admin actions, plus the two destructive ones that need
nothing else: the library reset and the model-cache deletes.

Every action requires confirm=True and refuses while any background job
runs, a restore or reset holds the library, or a maintenance operation is in
progress. The package and GPU torch actions that use this guard are in
diagnostics_packages_service and diagnostics_gpu_torch_service.

No HTTP types: the refusals are ServiceError subclasses the API maps by type.
"""

import time

import background_jobs
from jobs import job_launch, job_runner
import db
import diagnostics
from lib.errors import ConflictError, InvalidInputError, NotFoundError, ServiceError

# ---------------------------------------------------------------------------
# Admin actions (router: local_only). Each requires confirm=True and
# refuses while any background job runs.
# ---------------------------------------------------------------------------

RESET_CONFIRM_TEXT = "RESET"


class AdminActionRefused(ServiceError):
    """Raised when an admin action is not confirmed, targets an unknown
    package, or jobs are running. The subclasses below carry the HTTP
    mapping (422 / 404 / 409); callers can keep catching this one."""


class AdminActionUnconfirmed(AdminActionRefused, InvalidInputError):
    pass


class AdminActionUnknownPackage(AdminActionRefused, NotFoundError):
    pass


class AdminActionJobsRunning(AdminActionRefused, ConflictError):
    pass


class AdminActionStale(AdminActionRefused, ConflictError):
    """The confirmed version is no longer what the last update check found,
    or a check/probe is already running (409)."""


class AdminActionNotPossible(AdminActionRefused, InvalidInputError):
    """The action can't work on this machine (no NVIDIA GPU, a driver too
    old for CUDA 12, an unsupported Python) or goes through another action
    (torch's Upgrade is the GPU PyTorch setup)."""


def guard(confirm: bool):
    """confirm=True; no exclusive hold (a library restore or reset) and no
    maintenance operation (bulk delete, storage cleanup) in progress; and
    no job running or queued here or (fresh job_records rows) in another
    process -- the same rule as the Library admin actions
    (library_admin_service._any_job_running)."""
    if confirm is not True:
        raise AdminActionUnconfirmed("Confirmation required.")
    from services import library_admin_service
    if job_launch.exclusive_active() or job_launch.maintenance_active():
        raise AdminActionJobsRunning(
            "A library restore, reset or cleanup is in progress; try again when it ends.")
    if library_admin_service.any_job_running():
        raise AdminActionJobsRunning(
            "A background job is running or queued; wait for it to finish.")


def _cancel_probe(job_id):
    """None outside a job; inside one, a callable that is true once the
    user pressed Cancel (also seen from another process, via job_records)."""
    return None if job_id is None else (lambda: background_jobs.is_cancel_requested(job_id))


def _under_install_hold(fn):
    """Holds the library exclusively while fn() runs, so no job, restore,
    reset, cleanup or second install can start in this API process
    mid-install (409 if the hold can't be taken). Jobs in another process
    are re-checked under the hold, as reset_library does."""
    if not job_launch.acquire_exclusive("Dependency install"):
        raise AdminActionJobsRunning(
            "A job, restore, cleanup or another install is in progress; try again when it ends.")
    try:
        from services import library_admin_service
        if library_admin_service.any_job_running():     # re-check under the hold
            raise AdminActionJobsRunning(
                "A background job is running or queued; wait for it to finish.")
        return fn()
    finally:
        job_launch.release_exclusive()


def reset_library(confirm: bool = False, confirm_text: str = None) -> dict:
    """Irreversible from inside the app, but the database and the titles'
    media are first kept beside the library (previous_library_service, the
    last reset only). It does not cancel running jobs; it refuses instead,
    so a caller must stop them first. `confirm_text`, when given (the API
    always gives it), must be exactly "RESET"."""
    if confirm_text is not None and confirm_text != RESET_CONFIRM_TEXT:
        raise AdminActionUnconfirmed(
            f'Resetting the library needs confirm=true and confirm_text "{RESET_CONFIRM_TEXT}".')
    guard(confirm)
    # Hold the library exclusively for the reset, as a restore does, so no
    # job can start mid-reset (start_job refuses while the hold is taken).
    if not job_launch.acquire_exclusive("Library reset"):
        raise AdminActionJobsRunning(
            "A job, restore or cleanup is in progress; try again when it ends.")
    try:
        from services import library_admin_service
        if library_admin_service.any_job_running():     # re-check under the hold
            raise AdminActionJobsRunning(
                "A background job is running or queued; wait for it to finish.")
        # A finished job's thread can still be writing to the database it
        # is about to delete and recreate.
        if not job_runner.wait_for_job_threads(10.0):
            raise AdminActionJobsRunning(
                "A background job is still finishing; try again in a moment.")
        from services import previous_library_service
        kept = previous_library_service.keep_before_reset()
        try:
            db.reset_library()
        except BaseException:
            previous_library_service.after_reset(kept, ok=False)
            raise
        previous_library_service.after_reset(kept, ok=True)
        background_jobs.clear_all_jobs()
    finally:
        job_launch.release_exclusive()
    return {"ok": True, "reset_at": time.time()}


# ---------------------------------------------------------------------------
# Model cache delete (Q14)
# ---------------------------------------------------------------------------

def _exclusive_delete(delete, failed: str):
    """Runs a model-cache delete while holding the library exclusively (as
    _run_pip does), so no job can start and load the model mid-delete; jobs
    in another process are re-checked under the hold."""
    if not job_launch.acquire_exclusive("Model cache delete"):
        raise AdminActionJobsRunning(
            "A job, restore, cleanup or install is in progress; try again when it ends.")
    try:
        from services import library_admin_service
        if library_admin_service.any_job_running():     # re-check under the hold
            raise AdminActionJobsRunning(
                "A background job is running or queued; wait for it to finish.")
        if not delete():
            raise ServiceError(failed)
    finally:
        job_launch.release_exclusive()


def delete_hf_revision(revision: str, confirm: bool = False) -> dict:
    """Deletes one cached Hugging Face revision, named by a commit hash the
    cache scan lists (anything else is NotFoundError, so a caller can only
    ever delete what the scan shows). Refuses while a job runs, since a
    job may be loading that model."""
    if not isinstance(revision, str) or not any(
            e["revision"] == revision for e in diagnostics.scan_hf_cache()):
        raise NotFoundError("No cached model with that revision.")
    guard(confirm)
    _exclusive_delete(lambda: diagnostics.delete_hf_cache_revision(revision),
                      "Couldn't delete that model; see the log for details.")
    return {"deleted": True, "name": revision}


def delete_model_file(folder: str, name: str, confirm: bool = False) -> dict:
    """Deletes one entry of a model folder (diagnostics.MODEL_FOLDERS: the
    torch.hub checkpoints, the audio-separator models). Only a name that
    folder's scan lists is accepted, so no path can be built from input."""
    if folder not in diagnostics.MODEL_FOLDERS or not isinstance(name, str) or not any(
            e["name"] == name for e in diagnostics.scan_model_folder(folder)):
        raise NotFoundError("No downloaded model file with that name.")
    guard(confirm)
    _exclusive_delete(lambda: diagnostics.delete_model_folder_entry(folder, name),
                      "Couldn't delete that model file; see the log for details.")
    return {"deleted": True, "name": name}
