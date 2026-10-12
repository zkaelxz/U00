"""
services/model_readiness_service.py -- whether the models a run will load are
already on disk, so a run started away from the PC never downloads one.

A household member must not start a multi-GB model download on the owner's
disk and home connection; the owner at the PC still can (owner decision
2026-10-12, docs/remote-access-decision.md). Each job-starting route builds
the list of models its run would load (the `for_*` functions, which mirror
the worker's own choices) through `require_ready` before any job starts: the
loaders decide inside a worker, where refusing would be too late.

Every check reads the local caches only: no network call, no import of the
heavy model libraries. A check that cannot tell counts as not downloaded,
which only ever refuses a member; the owner is never checked.
"""

import functools
import importlib.util
import json
import os
import zipfile

import whisper_models
from services.service_errors import ForbiddenError

NOT_DOWNLOADED_MESSAGE = ("This model is not downloaded yet. Ask the owner of the main PC "
                          "to download it first.")

_WEIGHT_FILES = ("model.safetensors", "pytorch_model.bin")
_WEIGHT_INDEXES = ("model.safetensors.index.json", "pytorch_model.bin.index.json")


class ModelNotDownloadedError(ForbiddenError):
    """Its own code so the client shows the sentence, not a generic 403."""

    code = "model_not_downloaded"


def require_ready(at_pc: bool, build, *args, **kwargs) -> None:
    """Raises ModelNotDownloadedError unless the caller is at the PC or every
    check `build(*args, **kwargs)` lists (zero-argument callables) finds its
    model on disk. The list is built only away from the PC, so the owner's
    run never depends on what the builders import."""
    if at_pc:
        return
    for ready in build(*args, **kwargs):
        if not ready():
            raise ModelNotDownloadedError(NOT_DOWNLOADED_MESSAGE)


# --- Cache checks ----------------------------------------------------------

def _hf_snapshot(repo_id: str):
    """The folder `from_pretrained(repo_id)` resolves offline: the snapshot
    refs/main points at, in the cache huggingface_hub itself uses."""
    base = os.path.join(whisper_models._hub_dir(), "models--" + repo_id.replace("/", "--"))
    try:
        with open(os.path.join(base, "refs", "main"), encoding="utf-8") as f:
            revision = f.read().strip()
    except OSError:
        return None
    folder = os.path.join(base, "snapshots", revision)
    return folder if revision and os.path.isdir(folder) else None


def _weights_complete(folder: str) -> bool:
    # A sharded checkpoint is complete only when every shard its index names
    # is there: an interrupted download leaves the index and some shards.
    for name in _WEIGHT_INDEXES:
        index = os.path.join(folder, name)
        if os.path.isfile(index):
            try:
                with open(index, encoding="utf-8") as f:
                    shards = set(json.load(f)["weight_map"].values())
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                return False
            return bool(shards) and all(os.path.isfile(os.path.join(folder, s)) for s in shards)
    return any(os.path.isfile(os.path.join(folder, n)) for n in _WEIGHT_FILES)


def hf_model_ready(repo_id: str) -> bool:
    """A transformers-style repo: config.json plus complete weights."""
    folder = _hf_snapshot(repo_id)
    return bool(folder) and os.path.isfile(os.path.join(folder, "config.json")) \
        and _weights_complete(folder)


def _hf_has(repo_id: str, *names) -> bool:
    folder = _hf_snapshot(repo_id)
    return bool(folder) and all(os.path.isfile(os.path.join(folder, n)) for n in names)


def _tree_has(folder: str, filename: str) -> bool:
    for _root, _dirs, files in os.walk(folder):
        if filename in files:
            return True
    return False


def whisper_ready(size: str, local_model_path=None) -> bool:
    if local_model_path:
        # The loader uses the configured folder as is and downloads nothing.
        return os.path.isdir(local_model_path)
    return whisper_models.is_whisper_model_cached(size)


def qwen_asr_ready() -> bool:
    import qwen3_native
    # get_backend builds every Qwen3-ASR backend at its default size.
    return hf_model_ready(qwen3_native.asr_repo_id("1.7B"))


def aligner_ready() -> bool:
    import qwen3_native
    return hf_model_ready(qwen3_native.ALIGNER_REPO)


def diarization_ready() -> bool:
    """load_pipeline tries community-1 first, so that one alone counts; 3.1
    also counts with the two models its config pulls in."""
    first, second = ("pyannote/speaker-diarization-community-1",
                     "pyannote/speaker-diarization-3.1")
    community = _hf_snapshot(first)
    if community and os.path.isfile(os.path.join(community, "config.yaml")) \
            and _tree_has(community, "pytorch_model.bin"):
        return True
    return (_hf_has(second, "config.yaml")
            and _hf_has("pyannote/segmentation-3.0", "pytorch_model.bin")
            and _hf_has("pyannote/wespeaker-voxceleb-resnet34-LM", "pytorch_model.bin"))


def omnivoice_ready() -> bool:
    import dub
    return hf_model_ready(dub.OMNIVOICE_REPO)


# OmniVoice transcribes a reference clip that has no transcript with this model.
_OMNIVOICE_ASR_REPO = "openai/whisper-large-v3-turbo"


def omnivoice_asr_ready() -> bool:
    return hf_model_ready(_OMNIVOICE_ASR_REPO)


def _torch_checkpoint(filename: str) -> bool:
    home = os.environ.get("TORCH_HOME") or os.path.join(
        os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache"),
        "torch")
    path = os.path.join(home, "hub", "checkpoints", filename)
    try:
        return os.path.getsize(path) > 0 and (not filename.endswith(".pt")
                                              or zipfile.is_zipfile(path))
    except OSError:
        return False


# torchaudio's MMS_FA bundle saves its weights under this torch.hub name.
_MMS_FA_CHECKPOINT = "model.pt"
# demucs' htdemucs model is one torch.hub checkpoint with this name.
_HTDEMUCS_CHECKPOINT = "955717e8-8726e21a.th"


def word_aligner_ready() -> bool:
    return _torch_checkpoint(_MMS_FA_CHECKPOINT)


def separation_ready(backend: str, use_gpu=None) -> bool:
    """use_gpu False makes audio-separator refuse and "auto" fall back to
    Demucs (audio_preprocess.separate_vocals); None leaves it to the library."""
    import audio_preprocess
    roformer = audio_preprocess._checkpoint_ok(
        os.path.join(audio_preprocess.MODEL_DIR, audio_preprocess.MEL_ROFORMER_VOCAL_MODEL))
    if backend == "audio_separator":
        return roformer
    if backend == "demucs":
        return _torch_checkpoint(_HTDEMUCS_CHECKPOINT)
    # "auto" tries audio-separator first whenever it is installed.
    if use_gpu is not False and importlib.util.find_spec("audio_separator") is not None:
        return roformer
    return _torch_checkpoint(_HTDEMUCS_CHECKPOINT)


def sensevoice_ready() -> bool:
    import sensevoice_tags
    root = os.environ.get("MODELSCOPE_CACHE") or os.path.join(
        os.path.expanduser("~"), ".cache", "modelscope", "hub")
    # ModelScope moved its layout to hub/models/<id> in newer releases.
    return any(os.path.isfile(os.path.join(root, *prefix, *sensevoice_tags.MODEL_ID.split("/"),
                                           "model.pt"))
               for prefix in (("models",), ()))


def _paddle_ready(lang: str) -> bool:
    from services import real_model_check_service
    if lang == "ch":
        return real_model_check_service._paddle_models_state() == "present"
    # Other languages are left to PaddleX's own model choice, which changes
    # with its version: only a recognition folder for the language counts.
    root = os.environ.get("PADDLE_PDX_CACHE_HOME", "").strip() or os.path.join(
        os.path.expanduser("~"), ".paddlex")
    try:
        names = [n.lower() for n in os.listdir(os.path.join(root, "official_models"))]
    except OSError:
        return False
    return (any(n.endswith("_det") for n in names)
            and any(n.startswith(f"{lang}_") and n.endswith("_rec") for n in names))


def ocr_ready(backend: str, paddle_lang: str) -> bool:
    import ocr
    if backend == "manga_ocr":
        return hf_model_ready("kha-white/manga-ocr-base")
    if backend == "paddle_vl_manga":
        return (hf_model_ready(ocr._PADDLE_VL_MANGA_REPO)
                and _hf_snapshot(ocr._PADDLE_VL_PROCESSOR_REPO) is not None)
    if backend == "paddle":
        return _paddle_ready(paddle_lang)
    return True   # Tesseract is a program the owner installs; nothing downloads.


def bubble_detector_ready() -> bool:
    return hf_model_ready("ogkalu/comic-text-and-bubble-detector")


# --- What each run loads ---------------------------------------------------

def _need(check, *args):
    return functools.partial(check, *args)


def for_whisper_size(size: str) -> list:
    return [_need(whisper_ready, size)]


def for_title_whisper(drama_id: int) -> list:
    """Runs that hear with the title's saved Whisper size and ignore a
    configured local model folder (retranscribe, autotune)."""
    import db
    from services import transcribe_service
    drama = db.get_drama(drama_id)
    return for_whisper_size(transcribe_service.stored_whisper_size(drama)) if drama else []


def for_fix_flagged(drama_id: int) -> list:
    import db
    drama = db.get_drama(drama_id)
    # Only a title with audio is re-heard; the rest is translation.
    return for_title_whisper(drama_id) if drama and drama.get("audio_filename") else []


def for_aligner() -> list:
    return [aligner_ready]


def for_diarization() -> list:
    return [diarization_ready]


def for_sensevoice() -> list:
    return [sensevoice_ready]


def _backend_needs(backend: str, refine_timing: bool, mixed_languages: bool) -> list:
    """A speech-detection backend (asr_backend.get_backend) in a VAD run."""
    if backend == "anime_whisper":
        return [_need(whisper_ready, whisper_models.ANIME_WHISPER_KEY)]
    needs = [qwen_asr_ready]
    if not mixed_languages and (backend == "qwen3_asr_long"
                                or (backend == "qwen3_asr_vad" and refine_timing)):
        needs.append(aligner_ready)
    return needs


def for_compare(drama_id: int, whisper_size=None, backend=None) -> list:
    """compare_hear_worker.hear for the candidate start_compare resolves."""
    import db
    from services import asr_options_service, compare_hear_worker, transcribe_service
    drama = db.get_drama(drama_id)
    if drama is None:
        return []
    size = whisper_size or transcribe_service.stored_whisper_size(drama)
    backend = backend or asr_options_service.stored_asr_backend(drama)
    if backend in compare_hear_worker.VAD_BACKENDS:
        return _backend_needs(backend, False, False)
    needs = for_whisper_size(size)
    if backend == "qwen3_asr":
        needs.append(qwen_asr_ready)
    return needs


def for_transcribe(drama_id: int, run_diarize: bool = False, source_language=None) -> list:
    """What transcribe_pipeline loads for the run start_transcribe_run would
    start for this title, plus the diarization run it chains to."""
    import db
    import qwen3_native
    from ocr import default_hardsub_backend
    from services import asr_options_service, settings_service, transcribe_pipeline, transcribe_service
    drama = db.get_drama(drama_id)
    if drama is None:
        return []
    language = source_language or drama.get("source_language") or "zh"
    mode = drama.get("transcript_mode") or "have_transcript"
    needs = []
    if mode == "hardsub_ocr":
        try:
            import hardsub_ocr
        except ImportError:
            return needs   # without OpenCV the run fails before any OCR model loads
        requested = drama.get("hardsub_ocr_backend") or default_hardsub_backend(language)
        backend, _note = hardsub_ocr.resolve_backend(requested, language)
        if backend == "paddle":
            needs.append(_need(_paddle_ready, hardsub_ocr._HARDSUB_PADDLE_LANG.get(language, "ch")))
    else:
        backend = asr_options_service.stored_asr_backend(drama)
        mixed = asr_options_service.get_mixed_languages()
        use_groq = bool(drama.get("use_groq"))
        size = transcribe_service.stored_whisper_size(drama)
        vad_run = mode == "whisper" and backend in transcribe_pipeline.VAD_BACKENDS
        mixed_whisper_run = mixed and mode == "whisper" and backend == "whisper" and not use_groq
        if drama.get("separate_vocals_first"):
            needs.append(_need(separation_ready, drama.get("separation_backend") or "auto",
                               bool(settings_service.get("use_gpu"))))
        if vad_run:
            needs += _backend_needs(backend, asr_options_service.get_vad_refine_timing(), mixed)
        elif not use_groq:
            needs.append(_need(whisper_ready, size,
                               settings_service.get("whisper_model_path") or None))
            if (mode == "whisper" and not mixed_whisper_run
                    and not whisper_models.supports_word_timestamps(size)
                    and not qwen3_native.aligner_language_problem(language)):
                needs.append(aligner_ready)
        if drama.get("realign_long_segments") and not vad_run and not mixed_whisper_run:
            needs.append(word_aligner_ready)
        if mode == "whisper" and backend == "qwen3_asr":
            needs.append(qwen_asr_ready)
        if mode != "whisper" and drama.get("alignment_method") == "qwen3_forced_align":
            needs.append(aligner_ready)
    if run_diarize and settings_service.resolve_key("hf_token"):
        needs.append(diarization_ready)
    return needs


def for_dub(drama_id: int, keep_background: bool = False, tts_engine: str = "omnivoice") -> list:
    import db
    import dub
    drama = db.get_drama(drama_id)
    if drama is None:
        return []
    needs = [omnivoice_ready]   # the only dub/narration voice engine
    clones = dub.clone_map_from_characters(db.list_characters(drama_id), db.drama_dir(drama_id),
                                           default_engine=tts_engine, speaker_labels=())
    if any(c.get("engine") == "omnivoice" and c.get("ref_audio") and not c.get("ref_text")
           for c in clones.values()):
        needs.append(omnivoice_asr_ready)
    if keep_background and drama.get("content_mode") != "novel_narration":
        needs.append(_need(separation_ready, drama.get("separation_backend") or "auto"))
    return needs


def for_scanlate(drama_id: int, detect_backend: str = "auto") -> list:
    import db
    import ocr
    from services import settings_service
    drama = db.get_drama(drama_id)
    if drama is None:
        return []
    language = drama.get("source_language") or "zh"
    backend = settings_service.resolve_ocr_backend(language)
    needs = [_need(ocr_ready, backend, ocr._PADDLE_LANG_BY_SOURCE.get(language, "ch"))]
    # "auto" uses the ML detector only once it is cached; "ml" downloads it.
    if detect_backend == "ml":
        needs.append(bubble_detector_ready)
    return needs
