"""whisper_models.py -- loads or releases the Whisper model on the right device and
explains why it fell back to the CPU. No UI dependency; core.transcribe_for_timing
and the services call into it."""

import os
import sys
from dataclasses import dataclass

import cuda_runtime_path
import model_pins


_whisper_model_cache = {}

# Speech-recognition models offered in the Workspace picker (faster-whisper
# names). large-v3 is the default by the owner's choice: on his streamer comparisons it
# reads better than turbo without any Whisper settings adjusted. It is a readability
# preference, not a measured accuracy gain: on Baihe's own VOD test turbo scored 16.4
# and large-v3 18.7 character errors per 100 on the first 5 minutes (noise about 2
# points), and docs/asr-experiments.md has the per-language results. turbo stays the
# fast option at about half the time. The labels say only what was measured.
WHISPER_MODELS = {
    "small": "small -- fastest, least accurate",
    "medium": "medium -- no faster or more accurate than turbo in our tests",
    "large-v3": "large-v3 -- default; reads best on streamer audio in the owner's comparisons, about 2x slower than turbo, ~3GB",
    "large-v3-turbo": "large-v3-turbo -- the fast option; close to large-v3 in our tests, about 2x faster, ~1.6GB",
    "ja-1.5b": ("Japanese (whisper-ja 1.5B) -- anime and games, opt-in, about 3 GB, needs a GPU; "
                "its licence is not declared"),
    "kotoba-v2": ("Japanese (kotoba-whisper v2.0) -- opt-in, about 1.5 GB, about 6x faster than "
                  "large-v3 on Japanese; line timing only"),
}
DEFAULT_WHISPER_SIZE = "large-v3"

# Sizes the Workspace pickers no longer offer (Live keeps them). The API and
# titles that saved one still accept them. tiny and base exist only here and in Live's
# list, not in WHISPER_MODELS.
HIDDEN_WHISPER_SIZES = ("tiny", "base", "small", "medium")

# Keys that name a pinned Hugging Face repo instead of a faster-whisper size. The
# repo and its full commit sha live only here: a client sends the key, never a repo
# id or revision, and a branch name could be moved after the files were reviewed.
PINNED_CT2_REPOS = {
    key: (repo, model_pins.revision(repo)) for key, repo in (
        ("ja-1.5b", "TransWithAI/whisper-ja-1.5B-ct2"),
        ("kotoba-v2", "kotoba-tech/kotoba-whisper-v2.0-faster"))
}

# anime-whisper (litagin/anime-whisper) is NOT a key of WHISPER_MODELS or PINNED_CT2_REPOS:
# it must not reach the size picker, Live, mixed-language or Benchmark Lab, and
# only asr_backend.AnimeWhisperBackend loads it, by this name. The CTranslate2 build is
# byte-identical to `tools/ja_whisper_models.py convert-anime-whisper` (same model.bin
# sha256). Personal, non-commercial use only: the training data forbids commercial use of
# models trained on it, whatever the model card's MIT licence says.
ANIME_WHISPER_KEY = "anime-whisper"
ANIME_WHISPER_PIN = {
    "repo": "quantumcookie/anime-whisper-ct2-fp16",
    "revision": model_pins.revision("quantumcookie/anime-whisper-ct2-fp16"),
    "model_bin_sha256": "3a20db4ea35c80e0e4302565560de93af7191b24c9e4dcfa67411ce33bc67d7e",
    # Calibrated on this 2-layer decoder. The stock config carries large-v3's heads, which
    # do not exist here; layer-0 heads align badly.
    "alignment_heads": [[1, 14], [1, 8], [1, 12]],
}
ANIME_WHISPER_MANIFEST = "baihe-manifest.json"

# Approximate download sizes in GB. A member can start these downloads, so free space
# is checked first rather than letting a full drive fail partway through.
_DOWNLOAD_GB = {ANIME_WHISPER_KEY: 1.5, "ja-1.5b": 3, "kotoba-v2": 1.5}
_DOWNLOAD_MARGIN_BYTES = 256 * 1024 ** 2

# Models that must not be asked for word timestamps. kotoba's CTranslate2 config copies
# large-v3's alignment_heads (decoder layers 7-19) onto a 2-layer decoder, so
# word_timestamps=True can fail inside ctranslate2. Their lines keep Whisper's segment
# times, which the Qwen3 forced aligner refines where it is installed.
WORD_TIMESTAMP_UNSUPPORTED = frozenset({"kotoba-v2"})


def supports_word_timestamps(model_size: str) -> bool:
    return model_size not in WORD_TIMESTAMP_UNSUPPORTED


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
    "kotoba-v2": LanguageRule(
        only_for=("ja",),
        reason="The kotoba-whisper v2.0 model was trained on Japanese only and is only for Japanese titles."),
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



def selectable_whisper_sizes(context: str) -> list:
    """The Whisper sizes a picker offers, in display order. `context` is "live" (its own
    list) or any Workspace picker ("transcribe", "compare"). One place decides this so
    the pickers cannot drift; server validation does not use it and keeps accepting the
    hidden sizes."""
    if context == "live":
        return list(LIVE_WHISPER_SIZES)
    return ["large-v3", "large-v3-turbo", *(k for k in WHISPER_MODELS if k not in HIDDEN_WHISPER_SIZES
                                            and k not in ("large-v3", "large-v3-turbo"))]


def forget_gpu_model(model_size: str, local_model_path: str = None):
    """Drops the cached GPU copy of this Whisper model after its CUDA failed
    at inference, so the next run loads afresh instead of failing on it
    again, and the copy's VRAM can be freed before the CPU retry loads."""
    import gc
    key = f"{local_model_path or model_size}_gpu"
    _whisper_model_cache.pop(key, None)
    _whisper_device_info.pop(key, None)
    gc.collect()


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
    from lib.model_offline import hf_hub_dir
    return hf_hub_dir()


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
    if model_size == ANIME_WHISPER_KEY:
        return _anime_whisper_ready(anime_whisper_folder())
    if model_size in PINNED_CT2_REPOS:
        return _pinned_snapshot(model_size) is not None
    return _generic_snapshot(model_size) is not None


def _generic_repo(model_size: str):
    return model_size if "/" in model_size else faster_whisper_repo(model_size)


def _generic_snapshot(model_size: str):
    """Folder of a faster-whisper size's download at exactly its pinned revision, or None.
    The exact repo faster-whisper names: another org's copy of the same size, or another
    revision of this one, is not the tested model."""
    repo_id = _generic_repo(model_size)
    revision = model_pins.REVISIONS.get(repo_id)
    if revision is None:
        return None
    folder = os.path.join(_hub_dir(), "models--" + repo_id.replace("/", "--"), "snapshots", revision)
    # Every required file, not just the weights: an interrupted download lacks
    # some, and faster-whisper fetches a missing tokenizer.json from the
    # unpinned openai/whisper-tiny repo.
    from lib.ct2_repo import CT2_LOAD_REQUIRED
    return folder if all(os.path.isfile(os.path.join(folder, n)) for n in CT2_LOAD_REQUIRED) else None


# faster-whisper's own size -> repo table (faster_whisper.utils._MODELS), for
# when the package is not importable; the package's table wins when it is.
_FASTER_WHISPER_REPOS = {
    **{size: f"Systran/faster-whisper-{size}" for size in (
        "tiny.en", "tiny", "base.en", "base", "small.en", "small", "medium.en", "medium",
        "large-v1", "large-v2", "large-v3")},
    "large": "Systran/faster-whisper-large-v3",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
}


def faster_whisper_repo(model_size: str):
    try:
        from faster_whisper.utils import _MODELS
    except ImportError:
        _MODELS = _FASTER_WHISPER_REPOS
    return _MODELS.get(model_size)


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


def _download_model(model_size: str, token) -> str:
    """Local folder of a faster-whisper model at its pinned revision, downloaded with `token`.
    faster-whisper's own download_model fetches whatever the branch holds now, so the
    same files it would fetch are requested here at the pinned commit instead."""
    import huggingface_hub
    repo_id = _generic_repo(model_size)
    revision = model_pins.revision(repo_id)
    try:
        return huggingface_hub.snapshot_download(
            repo_id, revision=revision, token=token, etag_timeout=30,
            allow_patterns=["config.json", "preprocessor_config.json", "model.bin",
                            "tokenizer.json", "vocabulary.*"])
    except Exception as exc:
        if model_pins.is_revision_gone(exc):
            raise ModelDownloadError(model_pins.revision_gone_message(repo_id)) from exc
        raise


def _refuse_without_room(folder: str, model_key: str) -> None:
    """Raises ModelDownloadError when `folder`'s drive can't hold the model plus a margin.
    The folder may not exist yet, so the nearest existing parent is measured. A drive
    that doesn't report its free space never blocks a download."""
    import shutil
    gb = _DOWNLOAD_GB[model_key]
    while not os.path.isdir(folder) and os.path.dirname(folder) != folder:
        folder = os.path.dirname(folder)
    try:
        free = shutil.disk_usage(folder).free
    except OSError:
        return
    if free < gb * 1024 ** 3 + _DOWNLOAD_MARGIN_BYTES:
        raise ModelDownloadError(f"Not enough free disk space for this model: it needs about "
                                 f"{gb:g} GB; free some space and try again.")


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
        _refuse_without_room(_hub_dir(), model_size)
        return huggingface_hub.snapshot_download(
            repo, revision=revision, token=token, allow_patterns=wanted, etag_timeout=30)
    except ModelDownloadError:
        raise
    except Exception as exc:
        if model_pins.is_revision_gone(exc):
            raise ModelDownloadError(model_pins.revision_gone_message(repo)) from exc
        if is_network_error(exc):
            raise
        # Fixed text: the raw error can carry URLs and local paths.
        raise ModelDownloadError(
            f"Couldn't download the '{model_size}' model ({type(exc).__name__}). "
            "Check your Hugging Face token in Settings and try again.") from exc


def anime_whisper_folder() -> str:
    import portable
    return os.path.join(portable.data_dir(), "model_cache", "anime_whisper")


def _sha256_of(path: str) -> str:
    import hashlib
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _anime_whisper_ready(folder: str) -> bool:
    """True when `folder` holds a download that passed _fetch_anime_whisper's checks at
    the pinned revision. The manifest is written only after the sha256 matched, so the
    weights are not hashed again on every load; a changed size still counts as not ready."""
    import json
    from lib.ct2_repo import CT2_REQUIRED
    try:
        with open(os.path.join(folder, ANIME_WHISPER_MANIFEST), encoding="utf-8") as f:
            manifest = json.load(f)
        weights = os.path.join(folder, "model.bin")
        return (manifest.get("revision") == ANIME_WHISPER_PIN["revision"]
                and manifest.get("model_bin_sha256") == ANIME_WHISPER_PIN["model_bin_sha256"]
                and manifest.get("model_bin_size") == os.path.getsize(weights)
                and all(os.path.isfile(os.path.join(folder, n)) for n in CT2_REQUIRED))
    except (OSError, ValueError, AttributeError):
        return False


def _finish_anime_whisper(partial: str) -> None:
    """Checks a finished download in `partial` and prepares it for use, or raises
    ModelDownloadError. Nothing in the folder is trusted until this passes: it must hold
    only regular files from the CTranslate2 allow-list, and model.bin must be the pinned
    bytes."""
    import json
    import shutil
    from lib.ct2_repo import CT2_ALLOWED
    # huggingface_hub's own bookkeeping folder for a local_dir download.
    shutil.rmtree(os.path.join(partial, ".cache"), ignore_errors=True)
    for name in os.listdir(partial):
        path = os.path.join(partial, name)
        if name not in CT2_ALLOWED or os.path.islink(path) or not os.path.isfile(path):
            raise ModelDownloadError(
                "Not using the anime-whisper download: it holds a file outside the expected "
                "CTranslate2 set. Nothing was installed.")
    weights = os.path.join(partial, "model.bin")
    if _sha256_of(weights) != ANIME_WHISPER_PIN["model_bin_sha256"]:
        raise ModelDownloadError(
            "The downloaded anime-whisper model.bin does not match the pinned checksum, so it "
            "was discarded. Try again; if it keeps failing, report it.")
    config_path = os.path.join(partial, "config.json")
    shutil.copy2(config_path, config_path + ".orig")
    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)
    config["alignment_heads"] = ANIME_WHISPER_PIN["alignment_heads"]
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
    with open(os.path.join(partial, ANIME_WHISPER_MANIFEST), "w", encoding="utf-8") as f:
        json.dump({"source_repo": ANIME_WHISPER_PIN["repo"],
                   "revision": ANIME_WHISPER_PIN["revision"],
                   "model_bin_sha256": ANIME_WHISPER_PIN["model_bin_sha256"],
                   "model_bin_size": os.path.getsize(weights)}, f, indent=2)


def _fetch_anime_whisper(token) -> str:
    """Local folder of the pinned anime-whisper CTranslate2 build, downloaded on first use.

    Same safety rules as _fetch_pinned_model, plus: real files in Baihe's own model
    folder (config.json is edited, which must not write through a cache link), a
    checksum on the weights, and a download that is built in a folder of its own and
    moved into place only after every check passed. A file lock covers check, download
    and install, so a second process waits and then finds the finished install instead
    of touching the first one's files."""
    folder = anime_whisper_folder()
    if _anime_whisper_ready(folder):
        return folder
    import shutil
    import tempfile
    from lib import file_lock
    from lib.ct2_repo import RepoFilesRefused, select_ct2_files
    import huggingface_hub
    repo, revision = ANIME_WHISPER_PIN["repo"], ANIME_WHISPER_PIN["revision"]
    parent = os.path.dirname(folder)
    os.makedirs(parent, exist_ok=True)
    partial = None
    try:
        with file_lock.hold(folder + ".lock"):
            if _anime_whisper_ready(folder):
                return folder
            info = huggingface_hub.HfApi().model_info(repo, revision=revision, token=token, timeout=30)
            if getattr(info, "sha", revision) != revision:
                raise ModelDownloadError("Not downloading anime-whisper: the repository did not "
                                         "return the pinned revision. Nothing was downloaded.")
            try:
                wanted = select_ct2_files(s.rfilename for s in info.siblings)
            except RepoFilesRefused as exc:
                raise ModelDownloadError(
                    f"Not downloading anime-whisper: {exc}. Nothing was downloaded.") from exc
            # Holding the lock, nobody else is downloading: a folder left here is from a dead run.
            for name in os.listdir(parent):
                if name.startswith("anime_whisper.") and name.endswith((".partial", ".old")):
                    shutil.rmtree(os.path.join(parent, name), ignore_errors=True)
            _refuse_without_room(parent, ANIME_WHISPER_KEY)
            partial = tempfile.mkdtemp(prefix="anime_whisper.", suffix=".partial", dir=parent)
            huggingface_hub.snapshot_download(
                repo, revision=revision, token=token, allow_patterns=wanted, local_dir=partial,
                etag_timeout=30)
            _finish_anime_whisper(partial)
            if os.path.lexists(folder):
                # Present but not ready (a broken or older install): set it aside rather than
                # delete files a running process might have open.
                os.replace(folder, tempfile.mkdtemp(prefix="anime_whisper.", suffix=".old", dir=parent))
            os.replace(partial, folder)
            if not _anime_whisper_ready(folder):
                raise ModelDownloadError("The anime-whisper install did not pass its own check.")
            return folder
    except ModelDownloadError:
        raise
    except Exception as exc:
        if model_pins.is_revision_gone(exc):
            raise ModelDownloadError(model_pins.revision_gone_message(repo)) from exc
        if is_network_error(exc):
            raise
        # Fixed text: the raw error can carry URLs and local paths.
        raise ModelDownloadError(
            f"Couldn't download the anime-whisper model ({type(exc).__name__}).") from exc
    finally:
        # Only this call's own folder; the install folder is never removed here.
        if partial:
            shutil.rmtree(partial, ignore_errors=True)


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
    cuda_runtime_path.ensure_cuda_runtime_on_path()
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


# Best first. CTranslate2 runs float16 only on compute capability 7.0 and up, so a
# GTX 10xx card asking for it fails the load and the run drops to the CPU even
# though the card can do int8.
_GPU_COMPUTE_PREFERENCE = ("float16", "int8_float16", "int8_float32", "int8", "float32")


def gpu_compute_type() -> str:
    """The best Whisper compute type the first CUDA device supports, or float16
    when CTranslate2 can't say (the load then fails and falls back as before)."""
    try:
        import ctranslate2
        supported = set(ctranslate2.get_supported_compute_types("cuda"))
    except Exception:
        return "float16"
    return next((t for t in _GPU_COMPUTE_PREFERENCE if t in supported), "float16")


def load_whisper_model(model_size: str, use_gpu: bool = False, local_model_path: str = None,
                        hf_token: str = None):
    """Loads (and on first use, downloads) a Whisper model.

    use_gpu: try CUDA with the best compute type the card supports
    (gpu_compute_type), falling back to CPU automatically if
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

    cuda_runtime_path.ensure_cuda_runtime_on_path()
    from lib import ort_telemetry
    ort_telemetry.disable()   # faster-whisper's voice detector is an ONNX session
    from faster_whisper import WhisperModel

    # An HF token isn't required for public models, but without one you get
    # anonymous rate limits and slower downloads -- and a warning saying so.
    # Passed to the download, not put in os.environ: a token changed in
    # Settings must apply at once, and child processes must not inherit it.
    _tok = hf_token or os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
    model_path = target
    from lib import model_offline
    # A run started away from the PC loads only what is on disk, at the
    # revision already there (lib/model_offline.py).
    offline = model_offline.active() and not local_model_path
    if offline and not is_whisper_model_cached(model_size):
        raise ModelDownloadError(model_offline.NOT_DOWNLOADED_MESSAGE)
    build_kwargs = {"local_files_only": True} if offline else {}

    def _build(device, compute_type):
        return WhisperModel(model_path, device=device, compute_type=compute_type, **build_kwargs)

    device_info = {"device": "cpu", "compute_type": "int8", "gpu_error": None}
    try:
        if model_size == ANIME_WHISPER_KEY:
            model_path = _fetch_anime_whisper(_tok)
        elif model_size in PINNED_CT2_REPOS and not local_model_path:
            model_path = _fetch_pinned_model(model_size, _tok)
        elif not local_model_path and _generic_repo(model_size):
            # Offline, the check above already found the pinned folder on disk.
            model_path = _generic_snapshot(model_size) if offline else _download_model(model_size, _tok)
        if use_gpu:
            try:
                compute_type = gpu_compute_type()
                model = _build("cuda", compute_type)
                device_info = {"device": "cuda", "compute_type": compute_type, "gpu_error": None}
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
