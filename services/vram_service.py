"""
services/vram_service.py -- check that a GPU model will fit
in free VRAM before loading it, so the user gets a plain message instead
of a raw CUDA out-of-memory error halfway through a job.

`check_fits(what, required_mb)` raises InsufficientVramError when the free
memory is known and too small; when it can't be measured (no torch, no
nvidia-smi) it allows the load, since a guess must never block a job that
would have worked. It never switches models or devices by itself (a
settled product decision: no automatic model switching); the message says
what the user can do.

Free memory comes from torch.cuda.mem_get_info() when torch is already
imported with CUDA available (it sees this process's own cache too), else
from nvidia-smi (diagnostics.external_gpu_load). `MODEL_VRAM_MB` holds
deliberately low estimates of what each model needs, so the check only
refuses loads that can't possibly fit.
"""

import sys

import memory_headroom

# Rough lower bounds (MB) for weights plus a working margin, half precision
# where the loader uses it. Low on purpose: see the module docstring.
MODEL_VRAM_MB = {
    "OmniVoice": 2000,
    "PaddleOCR-VL-For-Manga": 3000,
}


class InsufficientVramError(RuntimeError):
    """Raised before a load that can't fit. The message is for the user."""


def free_vram_mb():
    """Free GPU memory in MB, or None when it can't be measured."""
    torch = sys.modules.get("torch")
    if torch is not None:
        try:
            if torch.cuda.is_available():
                free, _total = torch.cuda.mem_get_info()
                return free / (1024 * 1024)
        except Exception:
            pass
    try:
        import diagnostics
        load = diagnostics.external_gpu_load()
    except Exception:
        load = None
    if load and load.get("memory_free_mb") is not None:
        return float(load["memory_free_mb"])
    return None


def check_fits(what: str, required_mb=None, free_mb=None) -> None:
    """Raises InsufficientVramError if `what` (a MODEL_VRAM_MB name, or any
    label with `required_mb`) clearly won't fit in free VRAM."""
    required = required_mb if required_mb is not None else MODEL_VRAM_MB.get(what)
    if not required:
        return
    free = free_vram_mb() if free_mb is None else free_mb
    if free is None:
        return
    if free >= required:
        # Also honours Settings > Keep free VRAM, the same check the ASR loaders use.
        memory_headroom.check_need(what, required, "vram", free_mb=free)
        return
    raise InsufficientVramError(
        f"Not enough free GPU memory to load {what}: it needs about "
        f"{required / 1024:.1f} GB and only {max(free, 0) / 1024:.1f} GB is free. "
        "Close other programs using the GPU (or wait for another Baihe job to "
        "finish), then try again.")
