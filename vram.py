"""
vram.py -- check that a GPU model will fit
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
from nvidia-smi (diagnostics_torch.external_gpu_load). `MODEL_VRAM_MB` holds
deliberately low estimates of what each model needs, so the check only
refuses loads that can't possibly fit.
"""

import contextlib
import sys
import threading
import time

import memory_headroom

# Rough lower bounds (MB) for weights plus a working margin, half precision
# where the loader uses it. Low on purpose: see the module docstring.
MODEL_VRAM_MB = {
    "OmniVoice": 2000,
    "Qwen3-TTS": 4000,      # the 1.7B Base model: about 3.4 GB of weights in half precision
    "Chatterbox": 3500,     # the multilingual model in full precision: about 3 GB of weights
    "PaddleOCR-VL-For-Manga": 3000,
    "Qwen/Qwen3-VL-4B-Instruct": 9000,   # about 8.9 GB of weights in half precision
    "Qwen/Qwen3-VL-8B-Instruct": 17500,  # about 17.5 GB of weights in half precision
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
        import diagnostics_torch
        load = diagnostics_torch.external_gpu_load()
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


# The comic detector and cleanup models keep a GPU copy in the server process (the CPU copy stays
# the master), and nothing else frees it between comic runs, so the card kept about 1.6 GB plus
# CUDA's cached blocks for a model nobody was using.
COMIC_GPU_IDLE_SECONDS = 120.0
_comic_lock = threading.Lock()
_comic_active = 0
_comic_last_use = 0.0
_comic_timer = None


def release_comic_gpu_copies() -> None:
    """Drops the comic detector's and eraser's GPU copies (each reloads from its CPU copy on
    the next page) and hands CUDA's cached memory back. Only touches modules already loaded."""
    for module_name, cache_name in (("scanlate.scanlate_lama", "_lama_gpu"),
                                    ("scanlate.scanlate_detect", "_bubble_ml_gpu")):
        module = sys.modules.get(module_name)
        if module is not None:
            getattr(module, cache_name).clear()
    import gc
    gc.collect()
    torch = sys.modules.get("torch")
    try:
        if torch is not None and torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass  # a broken CUDA install must not fail the page that just ran


def _comic_idle_check() -> None:
    global _comic_timer
    with _comic_lock:
        wait = COMIC_GPU_IDLE_SECONDS - (time.monotonic() - _comic_last_use)
        if _comic_active or wait > 0:
            _comic_timer = threading.Timer(max(wait, 1.0), _comic_idle_check)
            _comic_timer.daemon = True
            _comic_timer.start()
            return
        _comic_timer = None
        # Under the lock: a page that starts now waits for this to finish instead of
        # having its model freed mid-call.
        release_comic_gpu_copies()


@contextlib.contextmanager
def comic_gpu_use():
    """Held around each comic detector or eraser call: the idle release never frees a copy
    while one is running, and runs COMIC_GPU_IDLE_SECONDS after the last call ended."""
    global _comic_active, _comic_last_use, _comic_timer
    with _comic_lock:
        _comic_active += 1
    try:
        yield
    finally:
        with _comic_lock:
            _comic_active -= 1
            _comic_last_use = time.monotonic()
            if _comic_timer is None:
                _comic_timer = threading.Timer(COMIC_GPU_IDLE_SECONDS, _comic_idle_check)
                _comic_timer.daemon = True
                _comic_timer.start()
