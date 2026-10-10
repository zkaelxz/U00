"""
api/routers/diagnostics_installs_routes.py -- the long-running Diagnostics
actions that run as background jobs (thin; see
services/diagnostics_installs_service.py):

- Package install and the GPU PyTorch setup: `local_only()` + confirm=true,
  package names and the torch variant from the service's whitelists only.
  Each returns the job id at once (409 while any job, restore, reset,
  cleanup or install runs); the job holds the library exclusively until it
  ends or is cancelled. The latest state is `admin.diagnostics`.
- Deno, the JavaScript runtime yt-dlp needs. Status is
  `admin.diagnostics`; the install is `local_only()` + confirm=true, 409
  while any job, restore, cleanup or install runs. The download URL comes
  only from the service's static table (never the request).
- "Test first" for an update: `local_only()` + confirm=true + the
  target the last update check offered (409 otherwise); runs this app's
  tests against that version in a throwaway environment. The latest
  state is `admin.diagnostics`.

Progress is also visible through GET /api/jobs/{job_id}; cancel through
POST /api/jobs/{job_id}/cancel.
"""

from fastapi import APIRouter, Path

from api.auth import local_only, require_permission
from api.diagnostics_install_schemas import (DiagnosticsDenoInstallRequest,
                                             DiagnosticsDenoStatus,
                                             DiagnosticsDependencyInstallState,
                                             DiagnosticsJobStarted,
                                             DiagnosticsUpgradeCheckRequest,
                                             DiagnosticsUpgradeCheckState)
from api.schemas import (DiagnosticsAdminConfirm, DiagnosticsGpuTorchSetupRequest,
                         ErrorResponse)
from services import diagnostics_installs_service as svc

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])

_ERRS = {404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
         422: {"model": ErrorResponse}}
_PACKAGE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"


@router.get("/dependency-install", dependencies=[require_permission("admin.diagnostics")],
            response_model=DiagnosticsDependencyInstallState,
            summary="The latest package install / GPU PyTorch setup: package, job, output tail")
def get_dependency_install():
    return svc.get_dependency_install()


@router.post("/dependencies/{package}/install", dependencies=[local_only()],
             response_model=DiagnosticsJobStarted,
             summary="PC only: pip-install a whitelisted optional package as a background "
                     "job (confirm=true)",
             responses=_ERRS)
def post_install(body: DiagnosticsAdminConfirm,
                 package: str = Path(min_length=1, max_length=80, pattern=_PACKAGE_PATTERN)):
    return svc.start_dependency_install(package, confirm=body.confirm)


@router.post("/gpu-torch/setup", dependencies=[local_only()],
             response_model=DiagnosticsJobStarted,
             summary="PC only: install the matched torch/torchvision/torchaudio from the fixed "
                     "PyTorch index, then verify, as a background job (confirm=true)",
             responses=_ERRS)
def post_gpu_torch_setup(body: DiagnosticsGpuTorchSetupRequest):
    return svc.start_gpu_torch_setup(body.variant, confirm=body.confirm)


@router.get("/deno", dependencies=[require_permission("admin.diagnostics")],
            response_model=DiagnosticsDenoStatus,
            summary="JS runtime for yt-dlp, Deno on PATH or installed, the install job")
def get_deno():
    return svc.get_deno_status()


@router.post("/deno/install", dependencies=[local_only()],
             response_model=DiagnosticsJobStarted,
             summary="PC only: install Deno as a background job (confirm=true)",
             responses=_ERRS)
def post_deno_install(body: DiagnosticsDenoInstallRequest):
    return svc.start_deno_install(confirm=body.confirm)


@router.get("/upgrade-check", dependencies=[require_permission("admin.diagnostics")],
            response_model=DiagnosticsUpgradeCheckState,
            summary='The latest "Test first" run: package, target, output tail, verdict')
def get_upgrade_check():
    return svc.get_upgrade_check()


@router.post("/dependencies/{package}/test-upgrade", dependencies=[local_only()],
             response_model=DiagnosticsJobStarted,
             summary="PC only: test the update-check target in a throwaway environment "
                     "(confirm=true; background job, minutes)",
             responses=_ERRS)
def post_test_upgrade(body: DiagnosticsUpgradeCheckRequest,
                      package: str = Path(min_length=1, max_length=80,
                                          pattern=_PACKAGE_PATTERN)):
    return svc.start_upgrade_check(package, target=body.target, confirm=body.confirm)
