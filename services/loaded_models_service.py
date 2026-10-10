"""
services/loaded_models_service.py -- what is loaded right now, read-only.

Three independent sources, each best effort so one failing never hides the
others: Ollama's loaded models, this app's own in-process model caches, and
GPU memory. Plus the "Free app models" action. Responses carry model names,
sizes and booleans only -- never a path or a URL.
"""

import ipaddress
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from urllib.parse import urlsplit

import background_jobs
import core
import memory_headroom
from lib import http
from services import settings_service
from services.service_errors import ConflictError

DEFAULT_OLLAMA_URL = "http://localhost:11434"
# llama.cpp's server default; one fixed probe rather than another setting.
LLAMA_CPP_PORT = 8080
PROBE_TIMEOUT_SECONDS = 2
PROBE_MAX_BYTES = 1_000_000
NVIDIA_SMI_TIMEOUT_SECONDS = 5

_UNKNOWN_MEMORY = {"state": "unknown", "total_bytes": None, "free_bytes": None,
                   "reserved_bytes": 0}

BUSY_MESSAGE = ("A transcription or other GPU job is running. Free the models "
                "when it has finished.")


def is_loopback_url(url: str) -> bool:
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _loopback_get(url: str):
    # guard=None: the caller has already checked the address is loopback.
    # trust_env off: a configured proxy must never see (or be asked to
    # reach) a loopback probe.
    return http.get(url, timeout=PROBE_TIMEOUT_SECONDS, max_bytes=PROBE_MAX_BYTES, guard=None,
                    trust_env=False, max_error_bytes=1)


def _ollama_rows() -> dict:
    base = (settings_service.resolve_key("ollama_url") or DEFAULT_OLLAMA_URL).rstrip("/")
    # A remote Ollama is the user's choice for translation, but this panel
    # must not reach out to other machines just to look.
    if not is_loopback_url(base):
        return {"state": "not_local", "models": []}
    try:
        resp = _loopback_get(f"{base}/api/ps")
        if not resp.ok:
            return {"state": "unavailable", "models": []}
        loaded = json.loads(resp.body).get("models") or []
    except Exception:
        return {"state": "not_running", "models": []}
    models = []
    for item in loaded:
        size = int(item.get("size") or 0)
        models.append({"name": str(item.get("name") or item.get("model") or "?")[:120],
                       "size_bytes": size,
                       "vram_bytes": min(int(item.get("size_vram") or 0), size)})
    return {"state": "running", "models": models}


def _device_label(device: str) -> str:
    return "GPU" if device.startswith("cuda") or device == "gpu" else "CPU"


def _model_label(name: str) -> str:
    # A local model folder is a path; show only its last part.
    return name.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def _app_rows() -> dict:
    """Reads the caches as they are; loads nothing and imports nothing new."""
    models = []
    try:
        for key in list(core._whisper_model_cache):
            target = key.rsplit("_", 1)[0]
            info = core._whisper_device_info.get(key) or {}
            device = info.get("device") or ("cuda" if key.endswith("_gpu") else "cpu")
            models.append({"name": _model_label(target), "kind": "Whisper",
                           "device": _device_label(device)})
        asr = sys.modules.get("asr_backend")
        for key in list(getattr(asr, "_asr_model_cache", {})):
            device = key.rsplit("_", 1)[-1]
            name = "Moss transcribe + diarize" if key.startswith("moss_") \
                else f"Qwen3-ASR {key.rsplit('_', 1)[0]}"
            models.append({"name": name, "kind": "Speech recognition",
                           "device": _device_label(device)})
        aligner = sys.modules.get("forced_align")
        for key in list(getattr(aligner, "_aligner_model_cache", {})):
            models.append({"name": "Qwen3 forced aligner", "kind": "Alignment",
                           "device": _device_label(key)})
    except Exception:
        return {"state": "unavailable", "models": []}
    return {"state": "ok", "models": models}


def _gpu_from_torch():
    torch = sys.modules.get("torch")
    if torch is None or not torch.cuda.is_available():
        return None
    free, total = torch.cuda.mem_get_info()
    return {"state": "ok", "name": torch.cuda.get_device_name(0),
            "total_bytes": int(total), "free_bytes": int(free),
            "used_bytes": int(total - free)}


def _gpu_from_nvidia_smi():
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    out = subprocess.run(
        [exe, "--query-gpu=name,memory.total,memory.used,memory.free",
         "--format=csv,noheader,nounits"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=NVIDIA_SMI_TIMEOUT_SECONDS, check=True)
    name, total, used, free = [part.strip() for part in out.stdout.splitlines()[0].split(",")]
    mib = 1024 * 1024
    return {"state": "ok", "name": name, "total_bytes": int(total) * mib,
            "used_bytes": int(used) * mib, "free_bytes": int(free) * mib}


def _gpu_row() -> dict:
    # torch is only asked if something already imported it; nvidia-smi sees
    # memory used by other programs too (Ollama), which torch's own counter
    # alone would not.
    for source in (_gpu_from_nvidia_smi, _gpu_from_torch):
        try:
            row = source()
        except Exception:
            row = None
        if row:
            return row
    return {"state": "unknown"}


def _llama_cpp_running() -> bool:
    try:
        resp = _loopback_get(f"http://127.0.0.1:{LLAMA_CPP_PORT}/v1/models")
        return bool(resp.ok and isinstance(json.loads(resp.body).get("data"), list))
    except Exception:
        return False


def _gpu_job_running() -> bool:
    return any(job.get("gpu_touching") and job.get("status") in ("running", "queued")
               for job in background_jobs.list_all_jobs().values())


def _memory_row() -> dict:
    # Reads the same source the loaders' keep-free check uses, so the panel
    # shows what that check will see.
    return memory_headroom.status()


def _guarded(source, fallback):
    try:
        return source()
    except Exception:
        return fallback


def get_loaded_models() -> dict:
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ollama": _guarded(_ollama_rows, {"state": "unavailable", "models": []}),
        "app": _guarded(_app_rows, {"state": "unavailable", "models": []}),
        "gpu": _guarded(_gpu_row, {"state": "unknown"}),
        "memory": _guarded(_memory_row, {"vram": _UNKNOWN_MEMORY, "ram": _UNKNOWN_MEMORY}),
        "llama_cpp_running": _guarded(_llama_cpp_running, False),
        # Unknown job state must not read as "idle" for the free action.
        "gpu_job_running": _guarded(_gpu_job_running, True),
    }


def free_app_models() -> dict:
    """Drops the app's cached models. Refused while a GPU job runs: it may be
    using one of them, and the cache gives no way to tell which."""
    if _gpu_job_running():
        raise ConflictError(BUSY_MESSAGE)
    core.release_gpu_models()
    return get_loaded_models()
