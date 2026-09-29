"""
api/routers/diagnostics_gaps_routes.py -- the Diagnostics features the
read-only overview (diagnostics_routes.py) lacks (Streamlit retirement M1;
API batch 1). Thin: see services/diagnostics_gaps_service.py.

Reads are `admin.diagnostics`: setup checks (Q01), model cache (Q14, list
only), pyannote readiness (Q15; `check_access=true` asks Hugging Face with
the server-side token and returns booleans only), finished-job history
(Q09), log tail with keyword filter (Q18) and the support report (Q17).

Writes are `local_only()` plus `confirm=true`: dependency install and
upgrade (Q06, package names from the service's whitelist only) and the
library reset (Q20, also `confirm_text` "RESET"). Each refuses while any
background job runs (409). Deleting a cached model or Piper voice is also
`local_only()` + `confirm=true`, refused while a job runs, and takes only a
name the cache scan lists. The saved bug bundles are listed here
(`admin.diagnostics`); their delete is in delete_routes.py. Bundle replay,
benchmark and the App Assistant are not exposed.
"""

from typing import List

from fastapi import APIRouter, Path, Query

from api.auth import local_only, require_permission
from api.schemas import (DiagnosticsAdminConfirm, DiagnosticsBugBundle,
                         DiagnosticsCacheDeleteResult, DiagnosticsInstallResult,
                         DiagnosticsJobHistoryItem, DiagnosticsLogTail, DiagnosticsModelCache,
                         DiagnosticsPyannoteReadiness, DiagnosticsResetRequest,
                         DiagnosticsResetResult, DiagnosticsSetupChecks,
                         DiagnosticsSupportReport, ErrorResponse)
from services import diagnostics_gaps_service as svc

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])

_ERRS = {404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
         422: {"model": ErrorResponse}}
_PACKAGE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"


@router.get("/setup-checks", dependencies=[require_permission("admin.diagnostics")],
            response_model=DiagnosticsSetupChecks,
            summary="Python, ffmpeg, JS runtime, CUDA, project files, library writable")
def get_setup_checks():
    return svc.get_setup_checks()


@router.get("/model-cache", dependencies=[require_permission("admin.diagnostics")],
            response_model=DiagnosticsModelCache,
            summary="Hugging Face cache revisions and Piper voices (names and sizes)")
def get_model_cache():
    return svc.get_model_cache()


@router.get("/pyannote", dependencies=[require_permission("admin.diagnostics")],
            response_model=DiagnosticsPyannoteReadiness,
            summary="pyannote installed / HF token set / (check_access=true) gated models open")
def get_pyannote(check_access: bool = Query(False)):
    return svc.get_pyannote_readiness(check_access=check_access)


@router.get("/job-history", dependencies=[require_permission("admin.diagnostics")],
            response_model=List[DiagnosticsJobHistoryItem],
            summary="Finished jobs in this process, newest first, redacted")
def get_job_history():
    return svc.get_job_history()


@router.get("/log", dependencies=[require_permission("admin.diagnostics")],
            response_model=DiagnosticsLogTail,
            summary="The last n (max 200) redacted log lines, optionally keyword-filtered",
            responses={422: {"model": ErrorResponse}})
def get_log(n: int = Query(svc.LOG_TAIL_DEFAULT, ge=0, le=svc.LOG_TAIL_MAX),
            keyword: str = Query("", max_length=100)):
    return {"lines": svc.get_log_tail(n, keyword)}


@router.get("/support-report", dependencies=[require_permission("admin.diagnostics")],
            response_model=DiagnosticsSupportReport,
            summary="A redacted plain-text report safe to share")
def get_support_report():
    return {"report": svc.build_support_report()}


@router.post("/dependencies/{package}/install", dependencies=[local_only()],
             response_model=DiagnosticsInstallResult,
             summary="PC only: pip-install a whitelisted optional package (confirm=true)",
             responses=_ERRS)
def post_install(body: DiagnosticsAdminConfirm,
                 package: str = Path(min_length=1, max_length=80, pattern=_PACKAGE_PATTERN)):
    return svc.install_dependency(package, confirm=body.confirm)


@router.post("/dependencies/{package}/upgrade", dependencies=[local_only()],
             response_model=DiagnosticsInstallResult,
             summary="PC only: upgrade a whitelisted optional package (confirm=true)",
             responses=_ERRS)
def post_upgrade(body: DiagnosticsAdminConfirm,
                 package: str = Path(min_length=1, max_length=80, pattern=_PACKAGE_PATTERN)):
    return svc.upgrade_dependency(package, confirm=body.confirm)


@router.post("/reset-library", dependencies=[local_only()],
             response_model=DiagnosticsResetResult,
             summary='PC only: erase the whole library (confirm=true, confirm_text "RESET")',
             responses=_ERRS)
def post_reset_library(body: DiagnosticsResetRequest):
    return svc.reset_library(confirm=body.confirm, confirm_text=body.confirm_text)


@router.post("/model-cache/hf/{revision}/delete", dependencies=[local_only()],
             response_model=DiagnosticsCacheDeleteResult,
             summary="PC only: delete one cached Hugging Face model revision (confirm=true)",
             responses=_ERRS)
def post_delete_hf_revision(body: DiagnosticsAdminConfirm,
                            revision: str = Path(pattern=r"^[0-9a-f]{40}$")):
    return svc.delete_hf_revision(revision, confirm=body.confirm)


@router.post("/model-cache/piper/{voice}/delete", dependencies=[local_only()],
             response_model=DiagnosticsCacheDeleteResult,
             summary="PC only: delete one downloaded Piper voice (confirm=true)",
             responses=_ERRS)
def post_delete_piper_voice(body: DiagnosticsAdminConfirm,
                            voice: str = Path(min_length=1, max_length=120,
                                              pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")):
    return svc.delete_piper_voice(voice, confirm=body.confirm)


@router.get("/bug-bundles", dependencies=[require_permission("admin.diagnostics")],
            response_model=List[DiagnosticsBugBundle],
            summary="Saved bug-reproduction bundles, newest first (no frozen input; redacted)")
def get_bug_bundles():
    return svc.list_bug_bundles()
