"""Keep-free headroom for GPU memory (VRAM) and system RAM.

The owner shares one server with other programs (a media server doing
hardware transcoding, say). Settings > Advanced > Offline and performance lets them reserve some
graphics memory and RAM; before a local model loads, `check` compares the
model's estimated need and the memory that is free right now with that
reserve and refuses the load, in plain words, when it would eat into it.
A refusal is early and clean: nothing has been allocated yet, so there is
no out-of-memory crash halfway through a job.

Both the app and the CLI load models through the same loaders, which call
`before_load` / `check`, so they behave alike. Everything is off at 0 (the
default) and reads nothing then. When memory can't be read (no GPU, no
nvidia-smi, an unsupported OS) the check is skipped rather than blocking:
a guess must never stop a job that would have worked.

Sizes in ESTIMATES_MB are approximations of weights plus a working margin,
not measurements; the error text says so. Cloud engines and a remote Ollama
use none of this machine's memory and are never checked.
"""

import json
import os
import sys
from urllib.parse import urlsplit

from lib import http

MB = 1024 * 1024
KEEP_FREE_KEYS = {"vram": "keep_free_vram_gb", "ram": "keep_free_ram_gb"}
# Generous so a typo can't make a read fail; the write path also checks the
# machine's detected total.
MAX_KEEP_FREE_GB = 1024

# (VRAM MB when run on the GPU, RAM MB when run on the CPU).
_WHISPER_MB = {
    "tiny": (400, 300), "base": (500, 400), "small": (1100, 900), "medium": (2800, 2200),
    "large-v1": (4800, 3800), "large-v2": (4800, 3800), "large-v3": (4800, 3800),
    "large": (4800, 3800), "turbo": (2600, 2000), "large-v3-turbo": (2600, 2000),
    "distil-large-v3": (3200, 2500),
}
_QWEN_ASR_MB = {"0.6B": (2500, 2500), "1.7B": (5200, 5200)}
_ESTIMATES_MB = {
    "aligner": {"qwen3": (2000, 2000)},
    "separation": {"separator": (3000, 3000)},
}
_LABELS = {"whisper": "Whisper", "qwen_asr": "Qwen3-ASR", "aligner": "the Qwen3 forced aligner",
           "separation": "vocal separation"}
_MEMORY_WORDS = {"vram": ("graphics memory (VRAM)", "Keep free graphics memory"),
                 "ram": ("RAM", "Keep free RAM")}

_warned_unreadable = set()


class HeadroomError(RuntimeError):
    """Raised before a load that would break the keep-free reserve. The
    message is for the user."""


def reserved_mb(memory: str) -> float:
    """The configured reserve in MB (0 = off). A settings read problem means
    off: it must not change behaviour."""
    try:
        import db
        if not os.path.exists(db.DB_PATH):
            return 0.0  # no library yet means no saved setting; reading would create one
        from services import settings_service
        return max(0.0, float(settings_service.get(KEEP_FREE_KEYS[memory]) or 0)) * 1024
    except Exception:
        return 0.0


def _read_vram_mb(at_load: bool = False):
    # torch's mem_get_info creates a CUDA context if none exists, and that
    # context holds VRAM until the process exits: exactly what this setting
    # reserves. So only the load-time check, and only once CUDA is already
    # initialized by a job, may use it; the settings panel and Settings save
    # read nvidia-smi.
    torch = sys.modules.get("torch") if at_load else None
    if torch is not None:
        try:
            if torch.cuda.is_initialized():
                free, total = torch.cuda.mem_get_info()
                return total / MB, free / MB
        except Exception:
            pass
    try:
        import diagnostics_torch
        load = diagnostics_torch.external_gpu_load()
    except Exception:
        load = None
    if load and load.get("memory_total_mb") is not None and load.get("memory_free_mb") is not None:
        return float(load["memory_total_mb"]), float(load["memory_free_mb"])
    return None


def _read_ram_mb():
    try:
        if sys.platform == "win32":
            import ctypes

            class _MemoryStatus(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                            ("total_phys", ctypes.c_ulonglong), ("avail_phys", ctypes.c_ulonglong),
                            ("total_page", ctypes.c_ulonglong), ("avail_page", ctypes.c_ulonglong),
                            ("total_virtual", ctypes.c_ulonglong), ("avail_virtual", ctypes.c_ulonglong),
                            ("avail_extended", ctypes.c_ulonglong)]
            status = _MemoryStatus()
            status.length = ctypes.sizeof(_MemoryStatus)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return None
            return status.total_phys / MB, status.avail_phys / MB
        values = {}
        with open("/proc/meminfo", encoding="ascii", errors="replace") as fh:
            for line in fh:
                key, _, rest = line.partition(":")
                if key in ("MemTotal", "MemAvailable"):
                    values[key] = float(rest.split()[0]) / 1024  # kB -> MB
        if len(values) == 2:
            return values["MemTotal"], values["MemAvailable"]
    except Exception:
        pass
    return None


def read_memory_mb(memory: str, at_load: bool = False):
    """(total_mb, free_mb) for "vram" or "ram", or None when it can't be read.
    at_load: the caller is about to load a model (see _read_vram_mb)."""
    return _read_vram_mb(at_load) if memory == "vram" else _read_ram_mb()


def total_mb(memory: str):
    reading = read_memory_mb(memory)
    return reading[0] if reading else None


def _gb(mb: float) -> str:
    return f"{max(mb, 0) / 1024:.1f} GB"


def _warn_unreadable_once(memory: str) -> None:
    if memory in _warned_unreadable:
        return
    _warned_unreadable.add(memory)
    try:
        import applog
        applog.get_logger().warning(
            f"{_MEMORY_WORDS[memory][1]} is set but {_MEMORY_WORDS[memory][0]} can't be read on this "
            "machine, so the setting is not being enforced.")
    except Exception:
        pass


def check_need(label: str, need_mb, memory: str, free_mb=None) -> None:
    """Raises HeadroomError if loading `label` (about `need_mb` MB of
    `memory`: "vram" or "ram") would leave less than the reserve free.
    `free_mb` skips the read when the caller already has it.

    Check and load are not atomic: two GPU jobs running in parallel
    (gpu_max_parallel) can both pass on the same free memory. The reserve is
    a best-effort guard against one job's footprint, not a lock."""
    reserved = reserved_mb(memory)
    if reserved <= 0 or not need_mb:
        return
    if free_mb is None:
        reading = read_memory_mb(memory, at_load=True)
        if reading is None:
            _warn_unreadable_once(memory)
            return
        free_mb = reading[1]
    if free_mb - need_mb >= reserved:
        return
    word, setting = _MEMORY_WORDS[memory]
    raise HeadroomError(
        f"Not loading {label}: it needs about {_gb(need_mb)} of {word}, {_gb(free_mb)} is free, "
        f"and {_gb(reserved)} is kept free for other programs (Settings > Advanced > Offline and performance > {setting}). "
        "The needed size is an estimate. To go ahead: unload another model (Settings > Loaded now, "
        "once no job is running), use a smaller model or int8 where offered, or lower the setting.")


def whisper_key(name: str):
    """The known model size for a Whisper size name or an offline model
    folder (by its last path part, e.g. faster-whisper-large-v3), else None.
    Only this key ever reaches user text, never the folder path."""
    name = str(name or "").replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].lower()
    name = name.removeprefix("faster-whisper-").removeprefix("whisper-").removesuffix(".en")
    return name if name in _WHISPER_MB else None


def estimate_mb(kind: str, name: str):
    """(vram_mb, ram_mb) for a model, or None when it isn't known (a custom
    model folder, a new size): an unknown model is never blocked."""
    name = str(name or "")
    if kind == "whisper":
        return _WHISPER_MB.get(whisper_key(name))
    if kind == "qwen_asr":
        return _QWEN_ASR_MB.get(name)
    return _ESTIMATES_MB.get(kind, {}).get(name)


def check(kind: str, name: str, use_gpu: bool) -> None:
    """Headroom check for a local model about to load, on the GPU or the CPU."""
    need = estimate_mb(kind, name)
    if need is None:
        return
    if kind == "whisper":
        name = whisper_key(name)
    label = f"{_LABELS.get(kind, kind)} {name}" if kind in ("whisper", "qwen_asr") else _LABELS[kind]
    check_need(label, need[0] if use_gpu else need[1], "vram" if use_gpu else "ram")


def check_separation(use_gpu) -> None:
    """use_gpu=None leaves the device to the separator library, which takes
    CUDA whenever torch reports it. Mirror that without creating a CUDA
    context: only an already-imported torch is asked, and when the device
    can't be told the check is skipped rather than guessed."""
    if use_gpu is None:
        torch = sys.modules.get("torch")
        try:
            use_gpu = bool(torch is not None and torch.cuda.is_available())
        except Exception:
            return
    # A CPU run (torch sees no CUDA) is judged against system RAM and its reserve, not VRAM.
    check("separation", "separator", bool(use_gpu))


def before_load(kind: str, name: str, use_gpu: bool, cached: bool) -> None:
    """The ASR / aligner loaders' one hook. Freeing Ollama comes first so the
    check sees the memory that frees up; a cached model needs no check."""
    import ollama_unload
    ollama_unload.prepare_gpu_for_transcription(use_gpu)
    if not cached:
        check(kind, name, use_gpu)


def _is_cloud_tag(model: str) -> bool:
    return model.endswith("-cloud") or model.endswith(":cloud")


def _is_loopback(base_url: str) -> bool:
    try:
        host = (urlsplit(base_url).hostname or "").lower()
    except ValueError:
        return False
    return host in ("localhost", "127.0.0.1", "::1")


def _ollama_json(url: str) -> dict:
    # engine_backends.shared imports this module, so it can only be imported late.
    from engine_backends.shared import PROVIDER_RESPONSE_MAX_BYTES
    # guard=None: base_url is the Ollama address the user configured (loopback here).
    resp = http.get(url, timeout=3, max_bytes=PROVIDER_RESPONSE_MAX_BYTES, guard=None)
    if resp.status >= 400:
        raise ValueError(f"HTTP {resp.status}")
    return json.loads(resp.body)


def _ollama_size_mb_if_not_loaded(base_url: str, model: str):
    wanted = model if ":" in model else f"{model}:latest"

    def names(entries):
        return {str(e.get(k)) for e in entries or [] if isinstance(e, dict) for k in ("name", "model") if e.get(k)}

    try:
        if wanted in names(_ollama_json(f"{base_url}/api/ps").get("models")):
            return None  # already resident: it takes no more memory
        for entry in _ollama_json(f"{base_url}/api/tags").get("models") or []:
            if isinstance(entry, dict) and wanted in names([entry]):
                return float(entry.get("size") or 0) / MB or None
    except Exception:
        pass
    return None


def check_ollama(base_url: str, model: str) -> None:
    """Before a translation on a local Ollama model: a model Ollama has not
    loaded yet takes about its download size plus context cache. Ollama
    decides its own GPU/CPU split, so only the VRAM reserve is checked: it
    fills the GPU first."""
    if reserved_mb("vram") <= 0 or _is_cloud_tag(model) or not _is_loopback(base_url):
        return
    size_mb = _ollama_size_mb_if_not_loaded(base_url.rstrip("/"), model)
    if size_mb:
        check_need(f"the Ollama model {model}", size_mb * 1.1 + 512, "vram")


def status() -> dict:
    """Free / total / reserved bytes for the Settings panel. No paths."""
    out = {}
    for memory in ("vram", "ram"):
        reading = read_memory_mb(memory)
        out[memory] = {"state": "ok" if reading else "unknown",
                       "total_bytes": int(reading[0] * MB) if reading else None,
                       "free_bytes": int(reading[1] * MB) if reading else None,
                       "reserved_bytes": int(reserved_mb(memory) * MB)}
    return out
