"""
services/diagnostics_gpu_torch_service.py -- the Diagnostics GPU PyTorch
flow: the status read (NVIDIA GPU, driver, installed torch family and build,
mismatches), the CUDA check in a fresh interpreter, and the confirm-gated
setup of the matched torch/torchvision/torchaudio triple from its fixed
index.

The pip run itself is diagnostics_packages_service._run_commands, under the
same exclusive hold and guard as every other admin action.
"""

import sys
import threading

import background_jobs
from jobs import job_launch
import diagnostics_torch as gpu_torch
import install_plan
from lib import proc as proc_run
from diagnostics_report import redact_for_support
from services.diagnostics_gaps_service import default_project_root
from services.diagnostics_admin_service import (
    AdminActionJobsRunning, AdminActionNotPossible, AdminActionStale, _cancel_probe,
    _under_install_hold, guard,
)
from services.diagnostics_packages_service import _clear_update_cache, _pip, _run_commands

# The CUDA torch wheels are about 2.5 GB; allow a slow link far longer.
GPU_TORCH_TIMEOUT_SECONDS = 3600


def _torch_setup_commands(variant: str) -> list:
    return [(_pip("install", *args), GPU_TORCH_TIMEOUT_SECONDS)
            for args in gpu_torch.torch_setup_pip_args(variant, default_project_root())]


def _core_blocks(variant: str) -> list:
    return install_plan.torch_setup_blocks(variant)


# ---------------------------------------------------------------------------
# GPU PyTorch: status (read) and the matched-triple setup (local_only).
# ---------------------------------------------------------------------------

_VERIFY_LOCK = threading.Lock()


def verify_torch(blocking: bool = True, cancel=None) -> dict:
    """Imports torch/torchvision/torchaudio in a fresh interpreter (this
    process may hold an older torch) and reports versions and whether CUDA
    works. Error text is redacted. One check at a time: blocking=False
    raises AdminActionStale (409) instead of queueing behind another."""
    if not _VERIFY_LOCK.acquire(blocking=blocking):
        raise AdminActionStale("A CUDA check is already running; try again in a moment.")
    try:
        return _verify_torch_once(cancel) if cancel else _verify_torch_once()
    finally:
        _VERIFY_LOCK.release()


def _verify_torch_once(cancel=None) -> dict:
    try:
        proc = proc_run.run_captured(
            [sys.executable, "-c", gpu_torch.TORCH_VERIFY_SCRIPT],
            gpu_torch.TORCH_VERIFY_TIMEOUT_SECONDS, cancel=cancel)
        if proc.timed_out:
            data = {"error": "importing torch took too long"}
        elif proc.cancelled:
            data = {"error": "cancelled"}
        else:
            data = gpu_torch.parse_torch_verify_output(proc.stdout)
    except OSError as e:
        data = {"error": type(e).__name__}
    out = {k: data.get(k) for k in ("torch", "torchvision", "torchaudio", "cuda_build",
                                    "device")}
    out = {k: (redact_for_support(v)[:120] if v is not None else None) for k, v in out.items()}
    out["cuda_available"] = data.get("cuda_available") if isinstance(
        data.get("cuda_available"), bool) else None
    devices = data.get("devices")
    no_kernels = gpu_torch.unsupported_gpu_message(
        devices if isinstance(devices, list) else None, data.get("torch"))
    errors = [data[k] for k in ("error", "torchvision_error", "torchaudio_error") if data.get(k)]
    if no_kernels:
        errors.insert(0, no_kernels)
    out["error"] = redact_for_support(" | ".join(str(e) for e in errors))[:600] if errors else None
    return out


def _variant_row(variant: str) -> dict:
    spec = gpu_torch.TORCH_VARIANTS[variant]
    return {"variant": variant, "label": spec["label"], "index_url": spec["index_url"],
            "versions": dict(spec["versions"]), "needs_nvidia": spec["needs_nvidia"]}


def _python_supported() -> bool:
    lo, hi = gpu_torch.TORCH_SUPPORTED_PYTHON
    return lo <= tuple(sys.version_info[:2]) <= hi


def check_gpu_torch() -> dict:
    """get_gpu_torch_status plus a CUDA check in a fresh interpreter. It
    takes a CUDA context (VRAM), so it refuses (409) while a job, restore,
    cleanup or install runs, or while another check does."""
    from services import library_admin_service
    if (job_launch.exclusive_active() or job_launch.maintenance_active()
            or library_admin_service.any_job_running()):
        raise AdminActionJobsRunning(
            "Wait for running jobs (or the install) to finish before checking CUDA.")
    return get_gpu_torch_status(probe=True)


def get_gpu_torch_status(probe: bool = False) -> dict:
    """NVIDIA GPU and driver (nvidia-smi), the installed torch family and
    its build, mismatches, and the recommended matched triple. probe=True
    (check_gpu_torch) also imports torch in a subprocess to report
    torch.cuda.is_available() (a few seconds); otherwise "probe" is None."""
    nvidia = gpu_torch.nvidia_driver_info()
    versions = gpu_torch.torch_family_versions()
    problems = gpu_torch.torch_family_problems(versions)
    variant, reason = gpu_torch.recommended_gpu_variant() if nvidia else ("cpu", None)
    recommended = _variant_row(variant)
    driver = gpu_torch.driver_check(nvidia["driver_version"]) if nvidia else None
    installed = {n: versions[n]["version"] for n in gpu_torch.TORCH_FAMILY}
    if not installed["torch"]:
        state = "missing"
    elif problems:
        state = "mismatched"
    elif nvidia and versions["torch"]["build"] == "cpu":
        state = "cpu_on_gpu"
    elif all(installed[n] == recommended["versions"][n] for n in ("torch", "torchaudio")) and \
            installed["torchvision"] in (None, recommended["versions"]["torchvision"]):
        state = "recommended"
    else:
        state = "different"
    return {
        "nvidia": ({"found": True, "gpu_name": redact_for_support(nvidia["gpu_name"]),
                    "driver_version": nvidia["driver_version"], **driver}
                   if nvidia else {"found": False, "gpu_name": None, "driver_version": None,
                                   "status": "unknown", "recommended": None, "minimum": None}),
        "installed": [{"name": n, "version": versions[n]["version"],
                       "build": versions[n]["build"]} for n in gpu_torch.TORCH_FAMILY],
        "problems": problems,
        "state": state,
        "python_supported": _python_supported(),
        "recommended": recommended,
        "recommended_reason": reason,
        "variants": [_variant_row(v) for v in gpu_torch.TORCH_VARIANTS],
        "probe": verify_torch(blocking=False) if probe else None,
    }


def prepare_gpu_torch_setup(variant: str = None, confirm: bool = False) -> str:
    """The checks before a GPU PyTorch setup starts; returns the variant to
    install (a gpu_torch.TORCH_VARIANTS key; default: CUDA when an NVIDIA
    GPU answers, else CPU). Refuses (422) without an NVIDIA GPU for a CUDA
    variant, with a driver too old for CUDA 12, or on a Python the wheels
    don't cover."""
    guard(confirm)
    nvidia = gpu_torch.nvidia_driver_info()
    if variant is None:
        variant = gpu_torch.recommended_gpu_variant()[0] if nvidia else "cpu"
    if variant not in gpu_torch.TORCH_VARIANTS:
        raise AdminActionNotPossible("Unknown PyTorch variant.")
    spec = gpu_torch.TORCH_VARIANTS[variant]
    if not _python_supported():
        lo, hi = gpu_torch.TORCH_SUPPORTED_PYTHON
        raise AdminActionNotPossible(
            f"PyTorch {spec['versions']['torch']} has wheels for Python "
            f"{lo[0]}.{lo[1]} to {hi[0]}.{hi[1]} only.")
    if spec["needs_nvidia"]:
        if not nvidia:
            raise AdminActionNotPossible(
                "No NVIDIA GPU answered (nvidia-smi not found or failed). Install or update "
                "the NVIDIA driver first, or choose the CPU version.")
        drv = gpu_torch.driver_check(nvidia["driver_version"])
        if drv["status"] == "too_old":
            raise AdminActionNotPossible(
                f"NVIDIA driver {nvidia['driver_version']} is too old for CUDA 12 "
                f"(needs at least {drv['minimum']}, recommended {drv['recommended']}). "
                "Update the driver from nvidia.com, then try again.")
    return variant


def setup_gpu_torch(variant: str = None, confirm: bool = False, job_id: str = None) -> dict:
    """Installs the matched torch/torchvision/torchaudio triple for
    `variant` from its fixed index, then verifies it in a fresh interpreter
    (see prepare_gpu_torch_setup for the refusals). ok only when pip
    succeeded and the new torch imports as the expected version (and, for
    CUDA, sees the GPU). `job_id`: running as that background job, which
    holds the library and already passed prepare_gpu_torch_setup."""
    if job_id is None:
        variant = prepare_gpu_torch_setup(variant, confirm)
    spec = gpu_torch.TORCH_VARIANTS[variant]
    cancel = _cancel_probe(job_id)

    def run():
        blocks = _core_blocks(variant)
        if blocks:
            return {"ok": False, "output_tail": [], "hint": " ".join(blocks), "verify": None}
        result = _run_commands(_torch_setup_commands(variant), job_id=job_id)
        if not result["ok"]:
            result["verify"] = None
            return result
        result["verify"] = verify_torch(cancel=cancel) if cancel else verify_torch()
        if cancel is not None and cancel():
            raise background_jobs.JobCancelled(job_id)
        return result
    try:
        result = run() if job_id is not None else _under_install_hold(run)
    finally:
        _clear_update_cache()
    verify = result["verify"]
    if verify is not None:
        good = verify.get("torch") == spec["versions"]["torch"] and not verify.get("error")
        if spec["needs_nvidia"]:
            good = good and verify.get("cuda_available") is True
        if not good:
            result["ok"] = False
            no_kernels = (verify.get("error") or "").startswith(gpu_torch.NO_KERNELS_PREFIX)
            result["hint"] = (
                "pip finished, but this PyTorch can't run on your GPU (see the message below)."
                if no_kernels else
                "pip finished, but the new PyTorch didn't pass the check "
                "(see below). Restart the app and open this section again; if "
                "CUDA still isn't available, update the NVIDIA driver.")
    result.update(package="torch", variant=variant)
    return result
