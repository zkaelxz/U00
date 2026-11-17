"""
services/model_download_service.py -- the Diagnostics "Download" button: fetch a
model's files now, so the first run (or a run from another device, which is
refused when its model is not on disk) finds them in the cache.

Only the models listed in CATALOG can be fetched, at the revisions
model_pins records; nothing in a request chooses a repo. The routes are
local_only(): like every first-use download, it spends the server PC's disk
and connection, so only a request from that PC may start one
(docs/remote-access-decision.md). A download is a background job, one at a
time, and "downloaded" is the same cache check a run's readiness test uses
(model_readiness_service), so the button and the refusal always agree.
"""

import functools
import os

import background_jobs
import model_pins
from diagnostics_report import redact_for_support
from jobs import job_launch
from lib.errors import ConflictError, InvalidInputError
from services import model_readiness_service as readiness

JOB_PREFIX = "model_download_"


def _qwen_repos() -> list:
    import qwen3_native
    # The repos get_backend builds by default (model_readiness_service.qwen_asr_ready).
    return [qwen3_native.asr_repo_id("1.7B"), qwen3_native.ALIGNER_REPO]


def _manga_ocr_repos() -> list:
    import ocr
    return [ocr.MANGA_OCR_REPO]


def _qwen_ready() -> bool:
    return readiness.qwen_asr_ready() and readiness.aligner_ready()


def _manga_ocr_ready() -> bool:
    return readiness.ocr_ready("manga_ocr", "")


# SenseVoiceSmall's weights are about 0.9 GB; only used to show a fraction.
_SENSEVOICE_APPROX_BYTES = 0.94e9
# ModelScope gives snapshot_download no hook for progress or a timeout, so the bytes that
# landed in its cache are watched instead. Two idle minutes means a blocked or crawling
# server, which would otherwise show "Running" for ever.
_STALL_SECONDS = 120
_POLL_SECONDS = 2


def _bytes_under(root: str, needle: str) -> int:
    total = 0
    for folder, _dirs, files in os.walk(root):
        if needle not in folder:
            continue
        for name in files:
            try:
                total += os.path.getsize(os.path.join(folder, name))
            except OSError:
                pass
    return total


def _modelscope_cache_root() -> str:
    return os.environ.get("MODELSCOPE_CACHE") or os.path.join(os.path.expanduser("~"), ".cache", "modelscope")


# The child is a separate process so a stalled ModelScope download can really be stopped:
# a thread inside snapshot_download cannot be cancelled and would keep running.
_MODELSCOPE_CHILD = ("import sys; from modelscope.hub.snapshot_download import snapshot_download; "
                     "snapshot_download(sys.argv[1])")
_DOWNLOAD_TIMEOUT_SECONDS = 60 * 60


def _fetch_modelscope(report, cancelled) -> None:
    import sys
    import time
    import job_process_kill
    import sensevoice_tags
    from lib.proc import stream_tree
    try:
        import modelscope.hub.snapshot_download  # noqa: F401  (checked here for a plain message)
    except ImportError as exc:
        raise ModelDownloadFailed(
            "SenseVoice needs its package first: install SenseVoice (FunASR) in "
            "Packages and installs.") from exc
    report(0.0, "Downloading SenseVoice…")
    root, needle = _modelscope_cache_root(), sensevoice_tags.MODEL_ID.split("/")[-1]
    state = {"bytes": -1, "changed": time.monotonic(), "checked": 0.0, "stalled": False}

    def watch() -> bool:
        if cancelled():
            return True
        now = time.monotonic()
        if now - state["checked"] < _POLL_SECONDS:
            return False
        state["checked"] = now
        got = _bytes_under(root, needle)
        if got != state["bytes"]:
            state["bytes"], state["changed"] = got, now
            report(min(got / _SENSEVOICE_APPROX_BYTES, 0.98), f"Downloading SenseVoice… {got / 1e6:.0f} MB so far")
        elif now - state["changed"] > _STALL_SECONDS:
            state["stalled"] = True
        return state["stalled"]

    end = {"returncode": None, "timed_out": False, "cancelled": False}
    last_line = ""
    for item in stream_tree([sys.executable, "-I", "-c", _MODELSCOPE_CHILD, sensevoice_tags.MODEL_ID],
                            _DOWNLOAD_TIMEOUT_SECONDS, cancel=watch, warn=job_process_kill._warn_via_jobs):
        if "line" in item:
            last_line = item["line"] or last_line
        else:
            end = item
    if state["stalled"]:
        raise ModelDownloadFailed(
            "SenseVoice downloads from ModelScope (not Hugging Face) and received nothing for "
            "2 minutes, so it was stopped. That server may be blocked or very slow from this "
            "network. Try again later or on another connection.")
    if end["cancelled"]:
        raise background_jobs.JobCancelled()
    if end["timed_out"] or end["returncode"] != 0:
        raise ModelDownloadFailed(f"SenseVoice download failed: {redact_for_support(last_line)[:200]}")


class ModelDownloadFailed(Exception):
    """A plain reason for the person; never carries a path or URL."""


def _fetch_hub(repos, report, cancelled, allow_patterns=None) -> None:
    try:
        import huggingface_hub
    except ImportError as exc:
        raise ModelDownloadFailed(
            "The Hugging Face download library is not installed. Install the Qwen3-ASR "
            "or manga-ocr package in Packages and installs first.") from exc

    for step, repo_id in enumerate(repos):
        received = [0]

        class Bar(huggingface_hub.utils.tqdm):
            def update(self, n=1):
                # The file-count bar has no unit "B"; only bytes are reported. A bar
                # the library disables never sets `unit` at all.
                if getattr(self, "unit", None) == "B" and n:
                    received[0] += n
                    report(step / len(repos), f"Downloading {repo_id}… {received[0] / 1e9:.1f} GB so far")
                    if cancelled():
                        raise background_jobs.JobCancelled()
                return super().update(n)

        report(step / len(repos), f"Downloading {repo_id}…")
        with model_pins.fetching(repo_id):
            huggingface_hub.snapshot_download(
                repo_id, revision=model_pins.revision(repo_id), etag_timeout=30, tqdm_class=Bar,
                allow_patterns=allow_patterns)


def _fetch_pyannote(report, cancelled) -> None:
    # Unpinned on purpose (model_pins): it is the owner's own gated download, the same files
    # pyannote's Pipeline.from_pretrained would fetch on the first run, but with the token
    # read from .env or the environment and a plain reason when Hugging Face refuses.
    import diarize
    from services import settings_service
    token = settings_service.resolve_key("hf_token")
    if not token:
        raise ModelDownloadFailed(diarize.NO_TOKEN_NOTICE)
    try:
        import huggingface_hub
    except ImportError as exc:
        raise ModelDownloadFailed(
            "The Hugging Face download library is not installed. Install pyannote.audio in "
            "Packages and installs first.") from exc
    repo_id = diarize.DIARIZATION_MODELS[0]
    report(0.0, f"Downloading {repo_id}…")
    try:
        huggingface_hub.snapshot_download(repo_id, token=token, etag_timeout=30)
    except Exception as exc:
        if diarize._hf_access_refused(exc):
            raise ModelDownloadFailed(
                f"Hugging Face refused access to {repo_id}. Sign in to the account that owns "
                f"your token, accept the terms at https://huggingface.co/{repo_id}, and make "
                "sure the token can read gated models. Then try again.") from exc
        raise


# Only what diarize_nemotron loads: the repo also holds a .nemo and a .gguf copy of the weights.
_NEMOTRON_FILES = ["config.json", "processor_config.json", "model.safetensors"]


def _fetch_nemotron(report, cancelled) -> None:
    import diarize_nemotron
    _fetch_hub([diarize_nemotron.MODEL_ID], report, cancelled, allow_patterns=_NEMOTRON_FILES)


def _lama_ready() -> bool:
    from scanlate import scanlate_lama
    return scanlate_lama.lama_ml_weights_cached()


def _fetch_lama(report, cancelled) -> None:
    # One checkpoint file, not the whole repo: scanlate_lama loads only this file.
    from scanlate import scanlate_lama
    try:
        scanlate_lama._load_lama_generator(allow_download=True)
    except ImportError as exc:
        raise ModelDownloadFailed(
            "LaMa-manga needs torch, safetensors and huggingface_hub. Install them in "
            "Packages and installs first.") from exc
    report(0.99, "Downloaded LaMa-manga")


def _fetch_whisper(size: str):
    def fetch(report, cancelled) -> None:
        import whisper_models
        from services import settings_service
        report(0.0, f"Downloading {size}…")
        token = settings_service.resolve_key("hf_token")
        # The same fetch a first run uses, so the files land at the pinned revision a run checks for.
        if size == whisper_models.ANIME_WHISPER_KEY:
            whisper_models._fetch_anime_whisper(token)
        elif size in whisper_models.PINNED_CT2_REPOS:
            whisper_models._fetch_pinned_model(size, token)
        else:
            whisper_models._download_model(size, token)
    return fetch


# The label names the model and its rough download size: Diagnostics lists these
# side by side, so a bare "Download" would not say which one it fetches.
# Whisper-family models need an entry because a run started from another device only
# loads what is already on disk, and a first-use download cannot happen there.
WHISPER_DOWNLOADS = {
    "whisper_large_v3": ("large-v3", "Whisper large-v3 (about 3 GB)"),
    "whisper_large_v3_turbo": ("large-v3-turbo", "Whisper large-v3-turbo (about 1.6 GB)"),
    "whisper_ja_1_5b": ("ja-1.5b", "Whisper Japanese 1.5B (about 3 GB)"),
    "whisper_kotoba_v2": ("kotoba-v2", "Whisper Japanese kotoba v2 (about 1.5 GB)"),
    "whisper_medium": ("medium", "Whisper medium (about 1.5 GB)"),
    "whisper_anime": ("anime-whisper", "anime-whisper, Japanese (about 1.5 GB)"),
    "whisper_breeze_asr_25": ("breeze-asr-25", "Breeze-ASR-25, Chinese (about 3 GB)"),
}


CATALOG = {
    "qwen3_asr": {
        "label": "Qwen3-ASR and its aligner",
        "ready": _qwen_ready,
        "fetch": lambda report, cancelled: _fetch_hub(_qwen_repos(), report, cancelled),
    },
    "sensevoice": {
        "label": "SenseVoice",
        "ready": readiness.sensevoice_ready,
        "fetch": _fetch_modelscope,
    },
    "manga_ocr": {
        "label": "manga-ocr",
        "ready": _manga_ocr_ready,
        "fetch": lambda report, cancelled: _fetch_hub(_manga_ocr_repos(), report, cancelled),
    },
    "nemotron_diarization": {
        "label": "Nemotron-3-Diarization (speaker detection)",
        "ready": readiness.nemotron_ready,
        "fetch": _fetch_nemotron,
    },
    "pyannote_diarization": {
        "label": "pyannote speaker model community-1 (needs your Hugging Face token)",
        "ready": readiness.diarization_ready,
        "fetch": _fetch_pyannote,
    },
    "lama_manga": {
        "label": "LaMa-manga (Scanlate text cleanup)",
        "ready": _lama_ready,
        "fetch": _fetch_lama,
    },
    **{
        model_id: {
            "label": label,
            "ready": functools.partial(readiness.whisper_ready, size),
            "fetch": _fetch_whisper(size),
        }
        for model_id, (size, label) in WHISPER_DOWNLOADS.items()
    },
}


def job_id(model_id: str) -> str:
    return JOB_PREFIX + model_id


def get_downloads() -> dict:
    """Each downloadable model with whether it is on disk and its job id.
    A check that cannot tell counts as not downloaded."""
    models = []
    for model_id, entry in CATALOG.items():
        try:
            downloaded = bool(entry["ready"]())
        except Exception:
            downloaded = False
        models.append({"id": model_id, "label": entry["label"], "downloaded": downloaded,
                       "job_id": job_id(model_id)})
    return {"models": models}


def _running_other_than(model_id: str):
    for other in CATALOG:
        job = background_jobs.get_status(job_id(other))
        if other != model_id and job and job.get("status") in ("running", "queued"):
            return CATALOG[other]["label"]
    return None


def start_download(model_id: str) -> dict:
    entry = CATALOG.get(model_id)
    if entry is None:
        raise InvalidInputError("That model cannot be downloaded from here.")
    if entry["ready"]():
        raise ConflictError(f"{entry['label']} is already downloaded.")
    busy = _running_other_than(model_id)
    if busy:
        # Two multi-GB downloads at once would just slow each other down.
        raise ConflictError(f"{busy} is downloading. Wait for it to finish first.")
    jid = job_id(model_id)
    if not job_launch.start_job(jid, functools.partial(_download_job, model_id),
                                description=f"Downloading {entry['label']}", line_fields=()):
        raise ConflictError(f"{entry['label']} is already downloading.")
    return {"job_id": jid, "started": True}


def _download_job(model_id: str) -> None:
    entry = CATALOG[model_id]
    jid = job_id(model_id)

    def report(fraction, message):
        background_jobs.update_progress(jid, min(fraction, 0.99), message)

    def cancelled():
        return background_jobs.is_cancel_requested(jid)

    try:
        entry["fetch"](report, cancelled)
        if not entry["ready"]():
            raise ModelDownloadFailed(
                f"{entry['label']} finished downloading but its files are incomplete. "
                "Try again.")
    except background_jobs.JobCancelled:
        raise
    except Exception as exc:
        detail = str(exc) if isinstance(exc, ModelDownloadFailed) else (
            f"Couldn't download {entry['label']}: {redact_for_support(exc)[:200]}")
        background_jobs.set_result(jid, {"status": "failed", "detail": detail})
        raise
    background_jobs.set_result(jid, {"status": "ok"})
