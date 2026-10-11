"""whisper_models.py -- loads or releases the Whisper model on the right device and
explains why it fell back to the CPU. No UI dependency; core.transcribe_for_timing
and the services call into it."""

import os
import sys
from dataclasses import dataclass


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
    "ja-1.5b": ("Japanese (whisper-ja 1.5B) -- anime and games, opt-in, about 3 GB, needs a GPU; "
                "its licence is not declared"),
}
DEFAULT_WHISPER_SIZE = "large-v3-turbo"

# Keys that name a pinned Hugging Face repo instead of a faster-whisper size. The
# repo and its full commit sha live only here: a client sends the key, never a repo
# id or revision, and a branch name could be moved after the files were reviewed.
PINNED_CT2_REPOS = {
    "ja-1.5b": ("TransWithAI/whisper-ja-1.5B-ct2", "1527314b14da5bdf0d14e7328649d4fa26840188"),
}


@dataclass(frozen=True)
class LanguageRule:
    """Where a model is not recommended. Either `only_for` (every other language is
    excluded, including ones added later) or `not_for` (only these are). `reason` is the
    sentence shown beside the disabled choice and returned by the server's refusal."""
    reason: str
    only_for: tuple = ()
    not_for: tuple = ()


# One entry per model that is NOT recommended for some language; a model with no entry
# fits every language. An exclusion needs evidence in docs/asr-experiments.md, so a model
# the docs do not show to be worse for a language stays selectable there. Adding a model
# (an anime-whisper, say) is one WHISPER_MODELS label plus, if it is language-bound, one
# entry here. Live's own list (LIVE_WHISPER_SIZES) is separate and not gated by this.
WHISPER_LANGUAGE_RULES = {
    # "medium about 1.9 points worse" than turbo on Japanese, and slower (RTF 0.84 vs 0.56).
    # Korean and Chinese showed no clear gap, so it stays selectable there.
    "medium": LanguageRule(
        not_for=("ja",),
        reason="Whisper medium isn't recommended for Japanese: in our tests it made about 1.9 points "
               "more errors than large-v3-turbo and was slower."),
    "ja-1.5b": LanguageRule(
        only_for=("ja",),
        reason="The whisper-ja 1.5B model is a Japanese fine-tune and is only for Japanese titles."),
}


def whisper_model_language_problem(model_size: str, source_language: str):
    """Why this model is not offered for this language, or None when it is."""
    rule = WHISPER_LANGUAGE_RULES.get(model_size)
    if rule is None:
        return None
    if rule.only_for and source_language not in rule.only_for:
        return rule.reason
    if source_language in rule.not_for:
        return rule.reason
    return None


def whisper_model_fits_language(model_size: str, source_language: str) -> bool:
    return whisper_model_language_problem(model_size, source_language) is None


def whisper_language_exclusions() -> dict:
    """{model: {language: reason}} for every excluded pair: what the pickers read, so the
    table above is the only place the rules live."""
    from core import SOURCE_LANGUAGES
    out = {}
    for model in WHISPER_LANGUAGE_RULES:
        for language in SOURCE_LANGUAGES:
            problem = whisper_model_language_problem(model, language)
            if problem:
                out.setdefault(model, {})[language] = problem
    return out


# Live's choices. small stays its default: Live has to keep up with the stream
# on a CPU, and turbo takes about 1.5x small's time on the repo's CPU test.
LIVE_WHISPER_SIZES = ("tiny", "base", "small", "medium", "large-v3-turbo")


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


def _hub_dir() -> str:
    # huggingface_hub's own order: HF_HUB_CACHE overrides HF_HOME/hub. Looking
    # elsewhere would call a downloaded model missing, or an unpinned one present.
    explicit = os.environ.get("HF_HUB_CACHE")
    if explicit:
        return explicit
    hub = os.environ.get("HF_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache", "huggingface")
    return os.path.join(hub, "hub")


def _pinned_snapshot(model_size: str):
    """Folder of a pinned repo's download at exactly its pinned revision, or None.
    Another revision of the same repo is not the reviewed one, so it does not count."""
    repo, revision = PINNED_CT2_REPOS[model_size]
    folder = os.path.join(_hub_dir(), "models--" + repo.replace("/", "--"), "snapshots", revision)
    # Every required file, not just the weights: faster-whisper fetches a missing
    # tokenizer.json from the unpinned openai/whisper-tiny repo.
    from lib.ct2_repo import CT2_REQUIRED
    return folder if all(os.path.isfile(os.path.join(folder, n)) for n in CT2_REQUIRED) else None


def is_whisper_model_cached(model_size: str) -> bool:
    """Whether a model is already downloaded, so the UI can warn about a
    large download before starting rather than failing partway."""
    if model_size in PINNED_CT2_REPOS:
        return _pinned_snapshot(model_size) is not None
    hub_dir = _hub_dir()
    if not os.path.isdir(hub_dir):
        return False
    # Exact repo name: a substring match reads large-v3 as cached when only
    # large-v3-turbo is. Any org, since turbo is not published by Systran.
    repo_name = f"faster-whisper-{model_size}".lower()
    try:
        for repo in os.listdir(hub_dir):
            parts = repo.lower().split("--")
            if len(parts) != 3 or parts[0] != "models" or parts[2] != repo_name:
                continue
            # An interrupted download leaves the folder without the weights.
            snapshots = os.path.join(hub_dir, repo, "snapshots")
            if os.path.isdir(snapshots) and any(
                    os.path.isfile(os.path.join(snapshots, rev, "model.bin"))
                    for rev in os.listdir(snapshots)):
                return True
    except OSError:
        pass
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


def _download_model(model_size: str, token: str) -> str:
    """Local folder of a faster-whisper model, downloaded with `token`."""
    import inspect
    from faster_whisper import utils
    if "use_auth_token" in inspect.signature(utils.download_model).parameters:
        return utils.download_model(model_size, use_auth_token=token)
    # faster-whisper before 1.2 can't take a token, so fetch the same files
    # as its download_model would.
    import huggingface_hub
    repo_id = model_size if "/" in model_size else utils._MODELS[model_size]
    return huggingface_hub.snapshot_download(
        repo_id, token=token, allow_patterns=["config.json", "preprocessor_config.json",
                                              "model.bin", "tokenizer.json", "vocabulary.*"])


def _fetch_pinned_model(model_size: str, token) -> str:
    """Local folder of a pinned repo, listed before anything is downloaded.

    The listing is checked first because a repo owner can add a script or pickle
    next to the weights; at the pinned revision that cannot change, but the check
    keeps a mistaken pin from fetching one. Only the allowed files are requested."""
    cached = _pinned_snapshot(model_size)
    if cached:
        return cached
    from lib.ct2_repo import RepoFilesRefused, select_ct2_files
    import huggingface_hub
    repo, revision = PINNED_CT2_REPOS[model_size]
    try:
        info = huggingface_hub.HfApi().model_info(repo, revision=revision, token=token, timeout=30)
        try:
            wanted = select_ct2_files(s.rfilename for s in info.siblings)
        except RepoFilesRefused as exc:
            raise ModelDownloadError(
                f"Not downloading the '{model_size}' model: {exc}. Nothing was downloaded. "
                "This model is only used when its repository holds plain CTranslate2 files.") from exc
        return huggingface_hub.snapshot_download(
            repo, revision=revision, token=token, allow_patterns=wanted, etag_timeout=30)
    except ModelDownloadError:
        raise
    except Exception as exc:
        if is_network_error(exc):
            raise
        # Fixed text: the raw error can carry URLs and local paths.
        raise ModelDownloadError(
            f"Couldn't download the '{model_size}' model ({type(exc).__name__}). "
            "Check your Hugging Face token in Settings and try again.") from exc


_CUBLAS_DLL = "cublas64_12.dll"
GPU_RUNTIME_MISSING_REASON = (
    "The CUDA 12 runtime libraries that Whisper needs on the GPU (cuBLAS) aren't installed on this PC.")


def _load_windows_dll(name: str) -> None:
    import ctypes
    # winmode=0 keeps the plain PATH search ctranslate2 itself uses; the
    # default for WinDLL skips PATH and would report a false "missing".
    ctypes.WinDLL(name, winmode=0)


def whisper_gpu_runtime(loader=None) -> dict:
    """Whether the CUDA runtime DLL faster-whisper needs can be loaded here:
    {"status": "ok" | "missing" | "unknown", "reason": fixed text or None}.
    Never imports torch or loads a model, and never raises. "unknown" off
    Windows, without ctranslate2, or when ctranslate2 sees no CUDA device
    (nothing to run on the GPU anyway). The reason is fixed text so no path
    or raw exception text reaches an API response."""
    if sys.platform != "win32":
        return {"status": "unknown", "reason": None}
    try:
        import ctranslate2
        if int(ctranslate2.get_cuda_device_count()) < 1:
            return {"status": "unknown", "reason": None}
    except Exception:
        return {"status": "unknown", "reason": None}
    try:
        (loader or _load_windows_dll)(_CUBLAS_DLL)
    except OSError:
        return {"status": "missing", "reason": GPU_RUNTIME_MISSING_REASON}
    except Exception:
        return {"status": "unknown", "reason": None}
    return {"status": "ok", "reason": None}


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
    # Passed to the download, not put in os.environ: a token changed in
    # Settings must apply at once, and child processes must not inherit it.
    _tok = hf_token or os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
    model_path = target

    def _build(device, compute_type):
        return WhisperModel(model_path, device=device, compute_type=compute_type)

    device_info = {"device": "cpu", "compute_type": "int8", "gpu_error": None}
    try:
        if model_size in PINNED_CT2_REPOS and not local_model_path:
            model_path = _fetch_pinned_model(model_size, _tok)
        elif _tok and not local_model_path:
            model_path = _download_model(model_size, _tok)
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
