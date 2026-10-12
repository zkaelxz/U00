"""
services/cuda_runtime_service.py -- state of the Diagnostics "CUDA runtime for
GPU Whisper" row. The row is offered only when an NVIDIA GPU is present and
cuda_runtime_path finds no cuBLAS 12 / cuDNN 9 anywhere Whisper could load
them from; the install itself is an ordinary install job
(diagnostics_installs_service.start_cuda_runtime_install).
"""

import shutil

import cuda_runtime_path
from services import diagnostics_installs_service as installs


def get_status() -> dict:
    """{"missing", "show", "install", "job"}. Re-checks the folders on every call,
    so a finished install turns the row into "found" without a restart.
    `install` is the latest install when it was this row's, else None; `job`
    is its job (the field the shared status poller watches)."""
    runtime = cuda_runtime_path.ensure_cuda_runtime_on_path()
    missing = runtime["status"] == "missing" and shutil.which("nvidia-smi") is not None
    state = installs.get_dependency_install()
    mine = state if state["kind"] == "cuda_runtime" else None
    return {"missing": missing, "show": missing or mine is not None, "install": mine,
            "job": mine["job"] if mine else None}
