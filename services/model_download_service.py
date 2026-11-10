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


def _fetch_modelscope(report, cancelled) -> None:
    import sensevoice_tags
    try:
        from modelscope.hub.snapshot_download import snapshot_download
    except ImportError as exc:
        raise ModelDownloadFailed(
            "SenseVoice needs its package first: install SenseVoice (FunASR) in "
            "Packages and installs.") from exc
    report(0.0, "Downloading SenseVoice…")
    snapshot_download(sensevoice_tags.MODEL_ID)


class ModelDownloadFailed(Exception):
    """A plain reason for the person; never carries a path or URL."""


def _fetch_hub(repos, report, cancelled) -> None:
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
                # The file-count bar has no unit "B"; only bytes are reported.
                if self.unit == "B" and n:
                    received[0] += n
                    report(step / len(repos), f"Downloading {repo_id}… {received[0] / 1e9:.1f} GB so far")
                    if cancelled():
                        raise background_jobs.JobCancelled()
                return super().update(n)

        report(step / len(repos), f"Downloading {repo_id}…")
        with model_pins.fetching(repo_id):
            huggingface_hub.snapshot_download(
                repo_id, revision=model_pins.revision(repo_id), etag_timeout=30, tqdm_class=Bar)


def _lama_ready() -> bool:
    import scanlate_inpaint
    return scanlate_inpaint.lama_ml_weights_cached()


def _fetch_lama(report, cancelled) -> None:
    # One checkpoint file, not the whole repo: scanlate_inpaint loads only this file.
    import scanlate_inpaint
    try:
        scanlate_inpaint._load_lama_generator(allow_download=True)
    except ImportError as exc:
        raise ModelDownloadFailed(
            "LaMa-manga needs torch, safetensors and huggingface_hub. Install them in "
            "Packages and installs first.") from exc
    report(0.99, "Downloaded LaMa-manga")


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
    "lama_manga": {
        "label": "LaMa-manga (Scanlate text cleanup)",
        "ready": _lama_ready,
        "fetch": _fetch_lama,
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
