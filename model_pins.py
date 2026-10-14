"""model_pins.py -- the one table of tested Hugging Face model revisions.

Every model Baihe downloads from the Hub (Whisper sizes, Qwen3-ASR and its
aligner, the OCR and scanlate models) is fetched and loaded at exactly the
commit recorded here, never at a branch: a branch can move to files nobody
has tested. A repo missing from the table is an error, not a quiet fall back
to the newest files, and tests/test_model_pins.py fails when a loader names a
repo with no pin. Pyannote diarization (gated, the owner's own token) and the
frozen dub models are not pinned yet.

To move a pin: test the new revision on real audio or pages first, then
change its hash here; downloads already on disk at the old revision are not
used for it.
"""

import contextlib

# repo id -> full commit hash. Revisions were the repos' main branch on
# 2026-10-14; see the PR that added this table for where each was tested.
REVISIONS = {
    # faster-whisper sizes (faster_whisper.utils._MODELS names these repos).
    "Systran/faster-whisper-tiny": "d90ca5fe260221311c53c58e660288d3deb8d356",
    "Systran/faster-whisper-base": "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66",
    "Systran/faster-whisper-small": "536b0662742c02347bc0e980a01041f333bce120",
    "Systran/faster-whisper-medium": "08e178d48790749d25932bbc082711ddcfdfbc4f",
    "Systran/faster-whisper-large-v3": "edaa852ec7e145841d8ffdb056a99866b5f0a478",
    # Renamed on the Hub to dropbox-dash/faster-whisper-large-v3-turbo; the old
    # name still resolves and is the one faster-whisper's own table uses.
    "mobiuslabsgmbh/faster-whisper-large-v3-turbo": "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
    # Japanese CTranslate2 builds offered as whisper_models.PINNED_CT2_REPOS.
    "TransWithAI/whisper-ja-1.5B-ct2": "1527314b14da5bdf0d14e7328649d4fa26840188",
    "kotoba-tech/kotoba-whisper-v2.0-faster": "f44edd35eaeb2274e85ac7b31fb2c6f59ff1c4bc",
    "quantumcookie/anime-whisper-ct2-fp16": "8ad0b0053dd0af6e108eea290e6f50a3aea49008",
    # Qwen3-ASR and the forced aligner (qwen3_native).
    "Qwen/Qwen3-ASR-0.6B-hf": "7f1569a48a89f3e3f4dc3a5c9d28bddd903bc76c",
    "Qwen/Qwen3-ASR-1.7B-hf": "bcd2b5b7f32b480ab5790554cfa8347f246a14f3",
    "Qwen/Qwen3-ForcedAligner-0.6B-hf": "c07281df297b9905d24a508279258cccf987a064",
    # OCR (ocr.py) and scanlate (scanlate_detect.py, scanlate_inpaint.py).
    "jzhang533/PaddleOCR-VL-For-Manga": "1e8aa5f1dd90cc86fe9137c9c0b26ebde613cfe8",
    "PaddlePaddle/PaddleOCR-VL": "7fa00a8c55b735ba51ba49a9058f3f9c57a99a11",
    "kha-white/manga-ocr-base": "aa6573bd10b0d446cbf622e29c3e084914df9741",
    "ogkalu/comic-text-and-bubble-detector": "16e8a622f91fabc6b5b65c96d32d1183f8843546",
    "mayocream/lama-manga": "f91c85b26913b3e83f9877867b4c336da3675238",
}


class UnpinnedModelError(RuntimeError):
    """A loader asked for a repo with no tested revision."""


def revision(repo_id: str) -> str:
    try:
        return REVISIONS[repo_id]
    except KeyError:
        raise UnpinnedModelError(
            f"{repo_id} has no tested version recorded, so Baihe will not download it.") from None


def revision_gone_message(repo_id: str) -> str:
    return (f"The tested version of {repo_id} could not be found on Hugging Face, so nothing "
            "was downloaded. Baihe does not fall back to a newer, untested version; a Baihe "
            "update that pins a new version is needed.")


class RevisionGone(RuntimeError):
    """The pinned commit is no longer on the Hub; the message says so plainly."""


@contextlib.contextmanager
def fetching(repo_id: str):
    """Turns a vanished pinned revision, however deep the loader wrapped it, into
    RevisionGone with a plain message."""
    try:
        yield
    except Exception as exc:
        if is_revision_gone(exc):
            raise RevisionGone(revision_gone_message(repo_id)) from exc
        raise


def is_revision_gone(exc: BaseException) -> bool:
    """huggingface_hub's RevisionNotFoundError, matched by name so callers need
    not import huggingface_hub to check."""
    return any(type(e).__name__ == "RevisionNotFoundError" for e in _chain(exc))


def _chain(exc):
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__
