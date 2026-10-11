"""
api/routers/diagnostics_gaps_routes.py -- the Diagnostics features the
read-only overview (diagnostics_routes.py) lacks. Thin: see services/diagnostics_gaps_service.py.

Reads are `admin.diagnostics`: setup checks, model cache (list
only), pyannote readiness (`check_access=true` asks Hugging Face with
the server-side token and returns booleans only), finished-job history,
log tail with keyword filter, the support report and
install presets (packages grouped by task, installed versions, approx.
sizes, PyPI links) and GPU PyTorch status. `POST /gpu-torch/check` (the
status plus a CUDA check in a fresh Python; 409 while a job or another
check runs) and `POST /package-updates/check` are also `admin.diagnostics`;
the latter
asks PyPI (a fixed https://pypi.org/pypi/<dist>/json per static dist name,
with a timeout) only when called, and caches the answer for Upgrade.

Writes are `local_only()` plus `confirm=true`: dependency upgrade (package
names from the service's whitelist only; it takes the `target` the user
confirmed and is 409 when the last update check no longer says so) and the
library reset (also `confirm_text` "RESET"). Each refuses while any
background job runs (409). Dependency install and the GPU PyTorch setup (a
fixed variant; versions and index come from diagnostics.py's static table,
never the request) are background jobs: see diagnostics_installs_routes.py.
Deleting a cached model or model
file (torch.hub checkpoints, audio-separator models) is also
`local_only()` + `confirm=true`, refused while a job runs, and takes only a
name the cache scan lists. The benchmark and the App Assistant are not
exposed.
"""

from typing import Literal

from fastapi import APIRouter, Path, Query

from api.auth import local_only, require_permission
from api.schemas import (DiagnosticsAdminConfirm,
                         DiagnosticsCacheDeleteResult, DiagnosticsGpuTorchStatus,
                         DiagnosticsInstallPresets, DiagnosticsInstallResult,
                         DiagnosticsPackageUpdates, DiagnosticsUpgradeRequest,
                         DiagnosticsLogTail, DiagnosticsModelCache,
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
            summary="Hugging Face cache revisions, torch.hub checkpoints and "
                    "audio-separator models (names and sizes)")
def get_model_cache():
    return svc.get_model_cache()


@router.get("/pyannote", dependencies=[require_permission("admin.diagnostics")],
            response_model=DiagnosticsPyannoteReadiness,
            summary="pyannote installed / HF token set / (check_access=true) gated models open")
def get_pyannote(check_access: bool = Query(False)):
    return svc.get_pyannote_readiness(check_access=check_access)


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


@router.get("/install-presets", dependencies=[require_permission("admin.diagnostics")],
            response_model=DiagnosticsInstallPresets,
            summary="Packages grouped by task, with approx. sizes, PyPI links and caveats")
def get_install_presets():
    return svc.get_install_presets()


@router.post("/package-updates/check", dependencies=[require_permission("admin.diagnostics")],
             response_model=DiagnosticsPackageUpdates,
             summary="Ask PyPI for newer releases of installed packages; the newest one "
                     "constraints.txt and installed dependents allow (explicit click only)")
def post_package_updates_check():
    return svc.check_package_updates()


@router.get("/gpu-torch", dependencies=[require_permission("admin.diagnostics")],
            response_model=DiagnosticsGpuTorchStatus,
            summary="NVIDIA GPU/driver, installed torch family, recommended matched triple")
def get_gpu_torch():
    return svc.get_gpu_torch_status()


@router.post("/gpu-torch/check", dependencies=[require_permission("admin.diagnostics")],
             response_model=DiagnosticsGpuTorchStatus,
             summary="The same status plus a CUDA check (imports torch in a fresh Python; "
                     "409 while a job or another check runs)",
             responses={409: {"model": ErrorResponse}})
def post_gpu_torch_check():
    return svc.check_gpu_torch()


@router.post("/dependencies/{package}/upgrade", dependencies=[local_only()],
             response_model=DiagnosticsInstallResult,
             summary="PC only: upgrade a whitelisted optional package (confirm=true)",
             responses=_ERRS)
def post_upgrade(body: DiagnosticsUpgradeRequest,
                 package: str = Path(min_length=1, max_length=80, pattern=_PACKAGE_PATTERN)):
    return svc.upgrade_dependency(package, confirm=body.confirm, target=body.target)


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


@router.post("/model-cache/files/{kind}/{name}/delete", dependencies=[local_only()],
             response_model=DiagnosticsCacheDeleteResult,
             summary="PC only: delete one torch.hub checkpoint or audio-separator model file "
                     "(confirm=true)",
             responses=_ERRS)
def post_delete_model_file(body: DiagnosticsAdminConfirm,
                           kind: Literal["torch", "audio_separator"],
                           name: str = Path(min_length=1, max_length=200,
                                            pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")):
    return svc.delete_model_file(kind, name, confirm=body.confirm)
