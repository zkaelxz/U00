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
which only ever refuses a member; the owner is never checked. A run that
passes away from the PC then loads offline (lib/model_offline.py), so a newer
upstream revision is not fetched either.
"""

import functools
import importlib.util
import json
import os

import model_pins
import whisper_models
from lib import model_offline
from lib.model_offline import NOT_DOWNLOADED_MESSAGE
from lib.errors import ForbiddenError

_WEIGHT_FILES = ("model.safetensors", "pytorch_model.bin")
_WEIGHT_INDEXES = ("model.safetensors.index.json", "pytorch_model.bin.index.json")


class ModelNotDownloadedError(ForbiddenError):
    """Its own code so the client shows the sentence, not a generic 403."""

    code = "model_not_downloaded"


def require_ready(at_pc: bool, build, *args, **kwargs) -> None:
    """Raises ModelNotDownloadedError unless the caller is at the PC or every
    check `build(*args, **kwargs)` lists (zero-argument callables) finds its
    model on disk. The list is built only away from the PC, so the owner's
    run never depends on what the builders import.

    Also marks this request so the job it starts next loads offline away
    from the PC. FastAPI runs each handler in its own copy of the request's
    context, so the mark ends with the handler; the queue runner, which
    outlives one item, sets and resets it itself."""
    model_offline.set_offline(not at_pc)
    if at_pc:
        return
    for ready in build(*args, **kwargs):
        if not ready():
            raise ModelNotDownloadedError(NOT_DOWNLOADED_MESSAGE)


# --- Cache checks ----------------------------------------------------------

def _hf_snapshot(repo_id: str):
    # A pinned repo loads at its pin only, so another revision on disk is not ready.
    return model_offline.hf_snapshot(repo_id, model_pins.REVISIONS.get(repo_id))


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


def _tree_has(folder: str, filename: str) -> bool:
    for _root, _dirs, files in os.walk(folder):
        if filename in files:
            return True
    return False


_DIARIZATION_REPO = "pyannote/speaker-diarization-community-1"


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
    """community-1 only: offline, diarize.load_pipeline does not fall back
    to 3.1, whose sub-models it would otherwise fetch."""
    community = _hf_snapshot(_DIARIZATION_REPO)
    return bool(community) and os.path.isfile(os.path.join(community, "config.yaml")) \
        and _tree_has(community, "pytorch_model.bin")


def omnivoice_ready() -> bool:
    import dub
    return hf_model_ready(dub.OMNIVOICE_REPO)


# OmniVoice transcribes a reference clip that has no transcript with this model.
_OMNIVOICE_ASR_REPO = "openai/whisper-large-v3-turbo"


def omnivoice_asr_ready() -> bool:
    return hf_model_ready(_OMNIVOICE_ASR_REPO)


_torch_checkpoint = model_offline.torch_hub_checkpoint


# torchaudio's MMS_FA bundle saves its weights under this torch.hub name.
_MMS_FA_CHECKPOINT = "model.pt"


def word_aligner_ready() -> bool:
    return _torch_checkpoint(_MMS_FA_CHECKPOINT)


def _demucs_ready() -> bool:
    """The run's own fallback check (audio_preprocess.separate_vocals): the
    package must import and its weights must already be in the torch.hub cache."""
    import audio_preprocess
    return (importlib.util.find_spec("demucs") is not None
            and _torch_checkpoint(audio_preprocess.HTDEMUCS_CHECKPOINT))


def separation_ready(backend: str, use_gpu=None) -> bool:
    """use_gpu False makes audio-separator refuse and "auto" fall back to
    Demucs (audio_preprocess.separate_vocals); None leaves it to the library."""
    import audio_preprocess
    roformer = audio_preprocess._checkpoint_ok(
        os.path.join(audio_preprocess.MODEL_DIR, audio_preprocess.MEL_ROFORMER_VOCAL_MODEL))
    if backend == "audio_separator":
        return roformer
    if backend == "demucs":
        return _demucs_ready()
    # "auto" tries audio-separator first whenever it is installed.
    if use_gpu is not False and importlib.util.find_spec("audio_separator") is not None:
        # Offline the run skips a missing RoFormer download and uses cached Demucs.
        return roformer or (model_offline.active() and _demucs_ready())
    return _demucs_ready()


def sensevoice_ready() -> bool:
    import sensevoice_tags
    return model_offline.modelscope_model_dir(sensevoice_tags.MODEL_ID) is not None


def _paddle_ready(lang: str) -> bool:
    """PaddleX has no offline switch but loads a model folder it finds
    without fetching. Only Chinese pins the exact folders (ocr._PADDLE_CH_MODELS);
    other languages are left to PaddleX's own choice, which changes with its
    version, so they cannot be checked and count as not downloaded."""
    from services import real_model_check_service
    return lang == "ch" and real_model_check_service._paddle_models_state() == "present"


def ocr_ready(backend: str, paddle_lang: str) -> bool:
    import ocr
    if backend == "manga_ocr":
        return hf_model_ready(ocr.MANGA_OCR_REPO)
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


def for_live_follow(follow_id: int, principal) -> list:
    """The Whisper size a Start Live from this follow uses (start_from_follow)."""
    from services import live_follow_service as follows
    row = follows._require(principal, follow_id, writing=True)
    return for_whisper_size(follows._options(row, strict=True)["whisper_size"])


def for_title_whisper(drama_id: int) -> list:
    """Runs that hear with the title's saved Whisper size and ignore a
    configured local model folder (retranscribe, autotune)."""
    import db
    from services import transcribe_options_service
    drama = db.get_drama(drama_id)
    return for_whisper_size(transcribe_options_service.stored_whisper_size(drama)) if drama else []


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
    from services import asr_options_service, compare_hear_worker, transcribe_options_service
    drama = db.get_drama(drama_id)
    if drama is None:
        return []
    size = whisper_size or transcribe_options_service.stored_whisper_size(drama)
    backend = backend or asr_options_service.stored_asr_backend(drama)
    if backend in compare_hear_worker.VAD_BACKENDS:
        return _backend_needs(backend, False, False)
    needs = for_whisper_size(size)
    if backend == "qwen3_asr":
        needs.append(qwen_asr_ready)
    return needs


def for_transcribe(drama_id: int, run_diarize: bool = False, source_language=None,
                   overrides=None) -> list:
    """What transcribe_pipeline loads for the run start_transcribe_run would
    start for this title (with a queued item's `overrides`), plus the
    diarization run it chains to."""
    import db
    import qwen3_native
    from ocr import default_hardsub_backend
    from services import asr_options_service, settings_service, transcribe_pipeline, transcribe_options_service
    drama = db.get_drama(drama_id)
    if drama is None:
        return []
    drama = transcribe_options_service.apply_run_overrides(drama, overrides)
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
        size = transcribe_options_service.stored_whisper_size(drama)
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


def for_dub(drama_id: int, tts_engine: str = "omnivoice") -> list:
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
    return needs


def for_scanlate_render() -> list:
    """Nothing to check: re-render and export use LaMa only once it is cached
    ("auto"). Going through require_ready still marks the job, so that cached
    load does not ask the Hub for a newer revision."""
    return []


def for_scanlate(drama_id: int, detect_backend: str = "auto", art_captions: bool = False) -> list:
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
    if art_captions and backend != "paddle":
        needs.append(_need(ocr_ready, "paddle", ocr._PADDLE_LANG_BY_SOURCE.get(language, "ch")))
    return needs
