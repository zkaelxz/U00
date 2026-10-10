"""whisper_models.py -- loads or releases the Whisper model on the right device and
explains why it fell back to the CPU. No UI dependency; core.transcribe_for_timing
and the services call into it."""

import os


_whisper_model_cache = {}

# Speech-recognition models offered in the Workspace picker (faster-whisper
# names). large-v3-turbo is the default: in our benchmarks (docs/asr-experiments.md)
# it matched large-v3 on Japanese, trailed it by about half a point on Korean and
# by more on clean Chinese, and ran about twice as fast. medium was never ahead
# of it. The labels and the Transcribe stage's note say only what was measured.
WHISPER_MODELS = {
    "small": "small -- fastest, least accurate",
    "medium": "medium -- no faster or more accurate than turbo in our tests",
    "large-v3": "large-v3 -- slightly more accurate on Korean and clean Chinese, about 2x slower, ~3GB",
    "large-v3-turbo": "large-v3-turbo -- default; close to large-v3 in our tests, about 2x faster",
}
DEFAULT_WHISPER_SIZE = "large-v3-turbo"


def release_gpu_models():
    """Call after a GPU stage (transcription, alignment, diarization)
    finishes: drops the cached Whisper / Qwen3-ASR / forced-aligner models
    and hands CUDA's cached memory back, so the next stage -- or a local
    translation model in Ollama, or TTS -- isn't fighting leftovers for
    the same VRAM. The next run of a stage reloads its model (seconds, from
    disk). Only touches modules that are already loaded, so it never
    imports torch or a model library just to clear it."""
    import gc
    import sys
    _whisper_model_cache.clear()
    _whisper_device_info.clear()
    for module_name, cache_name in (("asr_backend", "_asr_model_cache"),
                                    ("forced_align", "_aligner_model_cache")):
        module = sys.modules.get(module_name)
        if module is not None:
            getattr(module, cache_name).clear()
    gc.collect()
    torch = sys.modules.get("torch")
    if torch is not None:
        try:
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass  # a broken CUDA install must not fail the stage that just succeeded


class ModelDownloadError(RuntimeError):
    """Raised when a model can't be fetched, so callers can show a useful
    explanation instead of a Hugging Face stack trace."""


def diagnose_hostname(hostname: str = "huggingface.co") -> dict:
    """Checks whether a hostname resolves, and distinguishes the three
    outcomes that look alike from inside a stack trace:

      ok        - resolves normally
      blocked   - resolves to 0.0.0.0 / :: , which is what a DNS-level
                  blocker (Pi-hole, AdGuard, some corporate filters)
                  returns for a domain on a blocklist
      no_dns    - doesn't resolve at all

    'blocked' is worth calling out separately: the fix is whitelisting a
    domain on your own network, which is nothing like a broken connection.
    """
    import socket
    try:
        addrs = {ai[4][0] for ai in socket.getaddrinfo(hostname, None)}
    except Exception as exc:
        return {"status": "no_dns", "hostname": hostname, "addresses": [],
                "detail": f"{type(exc).__name__}: {exc}"}

    # Loopback is only a blackhole signal for a PUBLIC domain -- localhost
    # legitimately resolves to 127.0.0.1 and must not be flagged.
    loopback_names = {"localhost", "127.0.0.1", "::1"}
    blackholes = {"0.0.0.0", "::"}
    if hostname.lower() not in loopback_names:
        blackholes |= {"127.0.0.1", "::1"}

    if addrs and addrs.issubset(blackholes):
        return {"status": "blocked", "hostname": hostname, "addresses": sorted(addrs),
                "detail": (f"{hostname} resolves to {', '.join(sorted(addrs))}, which means a "
                           "DNS-level blocker on your network (Pi-hole, AdGuard, or similar) is "
                           "blocking it. Whitelist it there rather than changing anything here.")}
    return {"status": "ok", "hostname": hostname, "addresses": sorted(addrs), "detail": ""}


def is_gpu_error(exc: Exception) -> bool:
    """CUDA/cuBLAS/cuDNN library-loading and device errors.

    Distinct from _is_network_error and from a genuine audio/data
    problem. Matters because ctranslate2 (which faster-whisper wraps)
    defers ALL CUDA initialization until the first actual inference
    call -- constructing a WhisperModel(device="cuda") never touches
    the GPU, it just stores config. So a broken or missing CUDA
    install (a missing cublas64_12.dll, a driver/toolkit version
    mismatch, no CUDA-capable device at all) can only ever be caught
    here, at the point transcription actually runs -- not at model
    construction time, no matter how that's wrapped.
    """
    text = f"{type(exc).__name__}: {exc}".lower()
    markers = ("cublas", "cudnn", "cuda", "nvidia", "dll is not found",
               "cannot be loaded", "no cuda-capable device", "out of memory")
    return any(m in text for m in markers)


def is_network_error(exc: Exception) -> bool:
    """Whisper models download from Hugging Face on first use. A failure
    there is almost always network (DNS, firewall, proxy, VPN) rather
    than anything wrong with the audio or the app, and deserves a
    completely different message."""
    text = f"{type(exc).__name__}: {exc}".lower()
    markers = ("getaddrinfo", "connecterror", "localentrynotfound", "connection",
               "timed out", "timeout", "network", "temporary failure in name resolution",
               "max retries", "ssl", "proxy", "unreachable", "errno 11004",
               "client has been closed", "hf_hub", "huggingface", "name or service not known")
    return any(m in text for m in markers)


def is_whisper_model_cached(model_size: str) -> bool:
    """Whether a model is already downloaded, so the UI can warn about a
    large download before starting rather than failing partway."""
    hub = os.environ.get("HF_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache", "huggingface")
    hub_dir = os.path.join(hub, "hub")
    if not os.path.isdir(hub_dir):
        return False
    needle = f"faster-whisper-{model_size}".lower()
    try:
        return any(needle in d.lower() for d in os.listdir(hub_dir))
    except OSError:
        return False


_whisper_device_info = {}   # cache_key -> {"device", "compute_type", "gpu_error"}


def short_reason(exc, limit: int = 200) -> str:
    """One-line, secret-redacted description of an exception, for
    surfacing why the GPU couldn't be used."""
    from translate_engines import redact_secrets
    raw = exc if isinstance(exc, str) else f"{type(exc).__name__}: {exc}"
    text = " ".join(redact_secrets(raw).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def get_whisper_device_info(model_size: str, use_gpu: bool = False,
                            local_model_path: str = None) -> dict:
    """What load_whisper_model actually did for these arguments:
    {"device": "cuda"|"cpu", "compute_type", "gpu_error": <short reason or
    None>}. Empty dict if that model hasn't been loaded in this process."""
    key = f"{local_model_path or model_size}_{'gpu' if use_gpu else 'cpu'}"
    return dict(_whisper_device_info.get(key, {}))


def describe_whisper_device(info: dict) -> str:
    """Plain-words one-liner for get_whisper_device_info()'s dict."""
    if not info:
        return ""
    if info.get("gpu_error"):
        return f"GPU unavailable ({info['gpu_error']}); using CPU"
    if info.get("device") == "cuda":
        return f"Using GPU ({info.get('compute_type')})"
    return f"Using CPU ({info.get('compute_type')})"


def gpu_fallback_notice(task: str, reason: str) -> str:
    """The plain past-tense sentence every silent GPU->CPU fallback reports
    (job result, CLI line). `reason` is already one redacted line (short_reason)."""
    reason = " ".join(str(reason or "").split()).rstrip(".")
    why = f" ({reason})" if reason else ""
    return f"{task} ran on the CPU because the GPU couldn't be used{why}. This was slower than on the GPU."


def gpu_status() -> dict:
    """Whether ctranslate2 (faster-whisper) sees a CUDA device and whether
    torch.cuda is available. Never raises; each half is None when its
    library isn't installed, plus short redacted errors if a probe failed."""
    status = {"ctranslate2_cuda_devices": None, "torch_cuda_available": None, "errors": []}
    try:
        import ctranslate2
        status["ctranslate2_cuda_devices"] = int(ctranslate2.get_cuda_device_count())
    except ImportError:
        pass
    except Exception as exc:
        status["errors"].append("ctranslate2: " + short_reason(exc))
    try:
        import torch
        status["torch_cuda_available"] = bool(torch.cuda.is_available())
    except ImportError:
        pass
    except Exception as exc:
        status["errors"].append("torch: " + short_reason(exc))
    return status


def load_whisper_model(model_size: str, use_gpu: bool = False, local_model_path: str = None,
                        hf_token: str = None):
    """Loads (and on first use, downloads) a Whisper model.

    use_gpu: try CUDA with float16, falling back to CPU automatically if
    the GPU or the CUDA build of the runtime isn't available -- so
    enabling it on a machine without a GPU degrades rather than breaks.

    local_model_path: a directory containing an already-downloaded model.
    Lets the app work fully offline, or on a machine where the download
    is blocked, by fetching the model elsewhere and pointing at it.
    """
    target = local_model_path or model_size
    cache_key = f"{target}_{'gpu' if use_gpu else 'cpu'}"
    import memory_headroom as mh
    mh.before_load("whisper", target, use_gpu, cache_key in _whisper_model_cache)
    if cache_key in _whisper_model_cache:
        return _whisper_model_cache[cache_key]

    from faster_whisper import WhisperModel

    # An HF token isn't required for public models, but without one you get
    # anonymous rate limits and slower downloads -- and a warning saying so.
    _tok = hf_token or os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
    if _tok:
        os.environ.setdefault("HF_TOKEN", _tok)

    def _build(device, compute_type):
        return WhisperModel(target, device=device, compute_type=compute_type)

    device_info = {"device": "cpu", "compute_type": "int8", "gpu_error": None}
    try:
        if use_gpu:
            try:
                model = _build("cuda", "float16")
                device_info = {"device": "cuda", "compute_type": "float16", "gpu_error": None}
            except Exception as gpu_exc:
                if is_network_error(gpu_exc):
                    raise
                # No usable GPU: degrade, don't fail -- but remember why, so
                # callers can say the GPU was NOT used.
                device_info["gpu_error"] = short_reason(gpu_exc)
                model = _build("cpu", "int8")
        else:
            model = _build("cpu", "int8")
    except Exception as exc:
        if is_network_error(exc):
            diag = diagnose_hostname("huggingface.co")
            if diag["status"] == "blocked":
                raise ModelDownloadError(
                    f"Couldn't download the '{model_size}' model — huggingface.co is being "
                    f"blocked by a DNS blocker on your network.\n\n"
                    f"{diag['detail']}\n\n"
                    "Whitelist these (the cdn-lfs ones serve the actual model files, so "
                    "allowing only the first will fail mid-download):\n"
                    "  huggingface.co\n  cdn-lfs.huggingface.co\n  cdn-lfs-us-1.hf.co\n  hf.co\n\n"
                    "Then run: ipconfig /flushdns"
                ) from exc
            raise ModelDownloadError(
                f"Couldn't download the '{model_size}' speech-recognition model.\n\n"
                "This is a network problem, not a problem with your audio. The model is "
                "fetched from Hugging Face the first time you use it.\n\n"
                "Common causes on Windows:\n"
                "  - antivirus or firewall blocking Python's network access\n"
                "  - a VPN that's connected but not routing properly\n"
                "  - DNS not resolving huggingface.co\n\n"
                "Check with:  python -c \"import socket; print(socket.gethostbyname('huggingface.co'))\"\n\n"
                "If you can't get network access on this machine, download the model on "
                "another one and set a local model path in Settings."
            ) from exc
        raise

    _whisper_model_cache[cache_key] = model
    _whisper_device_info[cache_key] = device_info
    return model
