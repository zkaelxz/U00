"""
services/transcribe_pipeline.py -- the in-process transcription pipeline:
given an audio path and the resolved options, produce segments and lines
(vocal separation, model load, VAD, decode, Qwen pass, realign, split).

It writes nothing to the database: transcribe_service starts the job and
applies the returned outcome. _transcribe_worker is the spawn target of the
transcribe process job, so it stays a top-level function with plain
arguments and starts its own process group first (Windows starts it in a
fresh interpreter that imports only this module).
"""
import contextlib
import glob
import os
import shutil
import tempfile
import time
from typing import Optional

import asr_backend
import background_jobs
import core as core_module
import whisper_models
import long_line_split
import ollama_unload
import segment_splitting
import storage
from asr_backend import coverage_warning
from core import Line, align_transcript_to_timing, split_user_transcript, transcribe_for_timing
from services import settings_service
from translate_engines import redact_secrets

VAD_BACKENDS = ("qwen3_asr_vad", "qwen3_asr_long")


def _audio_duration_seconds(path) -> Optional[float]:
    """Best-effort audio length via ffprobe; None when it can't be read."""
    try:
        import media_inspect
        info = media_inspect.run_ffprobe(path, timeout=30)
        return float((info.get("format") or {}).get("duration") or 0.0) or None
    except Exception:
        return None


# A running job never reports 100%: only completion does. When Qwen3-ASR
# re-transcribes after Whisper, Whisper's stage fills 0..QWEN_SPLIT and
# Qwen3's batches fill QWEN_SPLIT..RUNNING_MAX; otherwise Whisper's stage
# fills 0..RUNNING_MAX.
RUNNING_MAX = 0.99
# Where the long-line split starts within the transcription stage's slice
# (a fraction of that slice's end), so its per-segment progress has room to move.
REALIGN_START = 0.9
QWEN_SPLIT = 0.85


def _raise_if_job_cancelled(job_id):
    if background_jobs.is_cancel_requested(job_id):
        raise background_jobs.JobCancelled(job_id)


# Fixed sentences, never the ImportError text: that names a module and reads
# as a crash. The Diagnostics page is where the install button is.
MISSING_TRANSCRIPTION_MESSAGE = "Transcription isn't installed yet. Open Diagnostics to install it."
_MISSING_QWEN_MESSAGE = "Qwen3 speech recognition isn't installed yet. Open Diagnostics to install it."
_MISSING_VAD_MESSAGE = ("Qwen3 speech detection needs transcription, which isn't installed yet. "
                        "Open Diagnostics to install it.")


def missing_package_outcome(message: str = MISSING_TRANSCRIPTION_MESSAGE) -> dict:
    """The pipeline outcome for a run that could not start for a missing package."""
    return {"failed_reason": "dependency_missing", "detail": message}


_MODEL_DOWNLOAD_SIZES = {"large-v3": "~3 GB", "large-v3-turbo": "~1.6 GB", "large-v2": "~3 GB", "large-v1": "~3 GB",
                         "large": "~3 GB", "medium": "~1.5 GB", "small": "~500 MB",
                         "base": "~150 MB", "tiny": "~75 MB"}


def _separation_device_label(kind: str) -> str:
    return {"gpu": "on GPU", "cpu": "on CPU (slow)"}.get(kind, "")


def _model_loading_message(whisper_size: str, cached: bool) -> str:
    if cached:
        return f"Loading Whisper model {whisper_size}..."
    size = _MODEL_DOWNLOAD_SIZES.get(whisper_size)
    hint = f", {size}" if size else ""
    return f"Loading Whisper model {whisper_size} (downloading on first use{hint})"


class _ThreadReporter:
    """Progress, stages and cancel for the pipeline running inside a thread
    job, which owns the job record directly."""

    def __init__(self, job_id):
        self.job_id = job_id

    def progress(self, frac, message=""):
        background_jobs.update_progress(self.job_id, frac, message)

    def stage(self, message, frac=0.0):
        return background_jobs.stage_ticker(self.job_id, message, frac=frac)

    def cancelled(self) -> bool:
        return background_jobs.is_cancel_requested(self.job_id)

    def raise_if_cancelled(self):
        _raise_if_job_cancelled(self.job_id)


class _ProcessStage:
    """stage_ticker's start/stop/with shape inside the worker process: the
    parent's watcher runs the real ticker (background_jobs.report_stage)."""

    def __init__(self, result_queue, message, frac):
        self._queue, self._message, self._frac = result_queue, message, frac
        self._open = False

    def start(self):
        if not self._open:
            self._open = True
            background_jobs.report_stage(self._queue, self._message, self._frac)
        return self

    def stop(self):
        """Idempotent."""
        if self._open:
            self._open = False
            background_jobs.report_stage(self._queue, "", self._frac)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
        return False


class _ProcessReporter:
    """The same inside the worker process: progress and stages go to the
    parent through the result queue. A cancel kills the process, so there is
    nothing to check here; the parent re-checks before applying anything.
    Every report and stage-boundary check ends the worker if its parent has
    died (background_jobs.exit_if_parent_gone)."""

    job_id = None

    def __init__(self, result_queue):
        self._queue = result_queue

    def progress(self, frac, message=""):
        background_jobs.report_progress(self._queue, frac, message)

    def stage(self, message, frac=0.0):
        return _ProcessStage(self._queue, message, frac)

    def cancelled(self) -> bool:
        background_jobs.exit_if_parent_gone()
        return False

    def raise_if_cancelled(self):
        background_jobs.exit_if_parent_gone()


def _transcribe_worker(audio_path, transcript_mode, transcript_text, source_language,
                       chinese_script, whisper_size, beam_size, min_silence_ms, vad_threshold,
                       separate_vocals_first, separation_backend, realign_long_segments,
                       whisper_fast_mode, use_groq, initial_prompt, use_gpu, asr_backend_choice,
                       alignment_method, local_model_path, qwen_batch_size, vad_refine_timing,
                       mixed_languages, hallucination_silence_sec, min_pause_sec, repeat_guard,
                       split_by_sentences, sensitivity_preset, voice_detector, scratch_dir,
                       result_queue):
    """Process-job target, started with spawn on every platform (top level
    and plain arguments only, so it pickles; nothing here may depend on
    state set up in the parent process after import): runs the pipeline for
    an audio transcript_mode and
    puts ("ok", outcome) -- the plain dict _apply_transcription takes -- or
    ("error", type name, redacted message). Writes nothing to the database.
    Every temp file goes under scratch_dir, which the parent removes however
    the run ends. The Groq key is read from the environment here, never
    passed in.

    Not covered by scratch_dir: model downloads go to their own caches. A
    cancel during audio-separator's first download of its model leaves a
    truncated file there (the library writes straight to the final path and
    skips a file that exists), and loading it fails on the next run until
    that file is deleted."""
    background_jobs.start_own_process_group()
    try:
        os.makedirs(scratch_dir, exist_ok=True)
        tempfile.tempdir = scratch_dir
        groq_api_key = settings_service.resolve_key("groq") if use_groq else None
        outcome = _transcribe_pipeline(
            _ProcessReporter(result_queue), audio_path, transcript_mode, transcript_text,
            source_language, chinese_script, whisper_size, beam_size, min_silence_ms,
            vad_threshold, separate_vocals_first, separation_backend, realign_long_segments,
            whisper_fast_mode, use_groq, groq_api_key, initial_prompt, use_gpu,
            asr_backend_choice, alignment_method, local_model_path=local_model_path,
            qwen_batch_size=qwen_batch_size, vad_refine_timing=vad_refine_timing,
            mixed_languages=mixed_languages, vocals_work_dir=scratch_dir,
            hallucination_silence_sec=hallucination_silence_sec, min_pause_sec=min_pause_sec,
            repeat_guard=repeat_guard, split_by_sentences=split_by_sentences,
            sensitivity_preset=sensitivity_preset, voice_detector=voice_detector)
        result_queue.put(("ok", outcome))
    except ImportError:
        result_queue.put(("ok", missing_package_outcome()))
    except Exception as exc:
        result_queue.put(("error", type(exc).__name__, redact_secrets(str(exc))))


_move_into_place = storage.move_into_place


def _remove_scratch_dir(path, _job_id=None, part_dir=None):
    shutil.rmtree(path, ignore_errors=True)
    if part_dir:
        # A worker killed during _move_into_place's cross-volume copy leaves
        # its .part- file beside vocals.wav, outside the scratch folder.
        for leftover in glob.glob(os.path.join(glob.escape(part_dir), ".part-*.wav")):
            with contextlib.suppress(OSError):
                os.remove(leftover)


def _transcribe_pipeline(rep, audio_path, transcript_mode, transcript_text, source_language,
                         chinese_script, whisper_size, beam_size, min_silence_ms, vad_threshold,
                         separate_vocals_first, separation_backend, realign_long_segments,
                         whisper_fast_mode, use_groq, groq_api_key, initial_prompt, use_gpu,
                         asr_backend_choice, alignment_method, local_model_path=None,
                         qwen_batch_size=1, video_path=None, hardsub_ocr_backend=None,
                         hardsub_interval=1.0, tesseract_cmd=None, vocals_work_dir=None,
                         vad_refine_timing=False, mixed_languages=False,
                         hallucination_silence_sec=core_module.DEFAULT_HALLUCINATION_SILENCE_SEC,
                         min_pause_sec=segment_splitting.MIN_WORD_GAP_SECONDS, repeat_guard=False,
                         split_by_sentences=False, sensitivity_preset="normal",
                         voice_detector="standard") -> dict:
    """Runs ASR (or hardsub OCR, thread jobs only) and returns a plain dict:
    {"failed_reason", ...} when nothing should be applied, else the lines
    and everything _apply_transcription needs. Touches no database row.
    `rep` is a _ThreadReporter or _ProcessReporter.

    Reuses transcribe_for_timing exactly as
    services.workspace_job_service.run_transcribe_job does (see that
    function's own docstring for the reasoning behind each parameter).

    local_model_path is Settings > Offline Whisper model folder (a folder
    holding an already-downloaded faster-whisper model, used instead of a
    Hugging Face download); qwen_batch_size is the saved Qwen3-ASR batch
    size; mixed_languages is the saved per-span language detection option (Whisper and
    "qwen3_asr_vad" backends only: other backends and Groq ignore it); vad_refine_timing is the saved forced-aligner timing option of the
    "qwen3_asr_vad" backend. vocals_work_dir: where vocal separation writes before its result
    is moved next to the audio, so a killed worker leaves no partial file.
    split_by_sentences: see docs/engine-backends.md.

    asr_backend_choice / alignment_method are the drama's stored
    choices: "qwen3_asr" only applies in whisper transcript_mode, and
    "qwen3_forced_align" only in have_transcript mode. Import/download/other Qwen3 failures end the job
    with a failed_reason ("dependency_missing", "model_download",
    "qwen3_asr"); a forced-align ValueError (e.g. an oversized line) falls
    back to the diff alignment and is reported as forced_align_error."""
    gpu_fallback_msg = []
    whisper_clock = {}
    stage_seconds = {}
    word_align_error = None
    forced_align_error = None
    coverage_msg = None
    detector_notices = []
    device_msg = ""
    device_suffix = ""
    # The other backends, and a supplied transcript, bring their own lines.
    sentence_lines = (split_by_sentences and transcript_mode == "whisper"
                      and asr_backend_choice in ("whisper", "qwen3_asr"))
    vad_run = transcript_mode == "whisper" and asr_backend_choice in VAD_BACKENDS
    # Where each Qwen3 model actually loaded ("Qwen3-ASR" -> "GPU"|"CPU"), so
    # a busy CPU is told apart from a silent GPU fallback.
    qwen_device = {}
    fallback_tasks = []
    vad_stage = {"ticker": None, "percent": False}

    def _qwen_on_fallback(task, exc):
        gpu_fallback_msg.append(whisper_models.short_reason(exc))
        fallback_tasks.append(task)

    def _qwen_on_device(task, label):
        qwen_device[task] = label

    def _on_vad_device(task, label):
        _qwen_on_device(task, label)
        # The model load ends the loading stage; until the first percent the
        # stage text says where the work runs.
        if task == "Qwen3-ASR" and vad_stage["ticker"] and not vad_stage["percent"]:
            _vad_set_stage(f"Transcribing with Qwen3-ASR on {label} (no percent until "
                           "the first batch finishes)")

    def _vad_set_stage(text):
        if vad_stage["ticker"]:
            vad_stage["ticker"].stop()
        vad_stage["ticker"] = rep.stage(text).start()

    if transcript_mode == "hardsub_ocr":
        import hardsub_ocr
        hardsub_info = {}
        segments = hardsub_ocr.extract_hardsub_subtitles(
            video_path, language=source_language, sample_interval=hardsub_interval,
            ocr_backend=hardsub_ocr_backend, chinese_script=chinese_script,
            tesseract_cmd=tesseract_cmd, job_id=rep.job_id, info=hardsub_info,
            cancel_check=rep.raise_if_cancelled,
            progress_cb=lambda frac: rep.progress(
                frac, f"Reading captions from video... {frac * 100:.0f}%"))
        if not segments:
            return {"failed_reason": "empty"}
        # OCR already produces real per-cue timing straight from the video --
        # no separate alignment step needed, same reasoning as the Whisper-
        # text-override branch below, just sourced from captions.
        lines = [Line(idx=i, start=seg["start"], end=seg["end"], zh=seg["text"])
                 for i, seg in enumerate(segments) if seg["text"].strip()]
        raw_backend, raw_model, raw_mode = "hardsub_ocr", hardsub_info.get("backend", hardsub_ocr_backend), "hardsub_ocr"
        dropped = hardsub_info.get("dropped_frames", 0)
        dropped_msg = (f"{dropped} sampled frame{'s' if dropped != 1 else ''} timed out while "
                       "reading and may be missing captions." if dropped else None)
        coverage_msg = " ".join(filter(None, [hardsub_info.get("note"), dropped_msg])) or None
    else:
        if separate_vocals_first:
            import audio_preprocess
            vocals_path = os.path.join(os.path.dirname(audio_path), "vocals.wav")
            sep = {"ticker": None, "device": ""}

            def _sep_message(frac):
                where = f" {sep['device']}" if sep["device"] else ""
                return f"Separating vocals{where}, {frac * 100:.0f}%"

            def _sep_event(event, value):
                if sep["ticker"]:
                    sep["ticker"].stop()
                    sep["ticker"] = None
                if event == "loading":
                    # The model download/load cannot report progress.
                    sep["ticker"] = rep.stage(
                        "Loading the vocal separation model (downloads on first use)").start()
                elif event == "device":
                    sep["device"] = _separation_device_label(value)
                    rep.progress(0.0, _sep_message(0.0))

            rep.progress(0.0, "Separating vocals from background music...")
            separate_started = time.monotonic()
            try:
                separated = audio_preprocess.separate_vocals(
                    audio_path, os.path.join(vocals_work_dir, "vocals.wav")
                    if vocals_work_dir else vocals_path, backend=separation_backend,
                    progress_cb=lambda frac: rep.progress(frac, _sep_message(frac)),
                    cancel_check_cb=rep.cancelled,
                    use_gpu=use_gpu, event_cb=_sep_event)
            except audio_preprocess.VocalSeparationCancelled:
                return {"failed_reason": "cancelled"}
            except audio_preprocess.VocalSeparationError as exc:
                return {"failed_reason": "vocal_separation", "detail": str(exc)}
            finally:
                if sep["ticker"]:
                    sep["ticker"].stop()
            stage_seconds["separate"] = time.monotonic() - separate_started
            if vocals_work_dir and os.path.abspath(separated) != os.path.abspath(vocals_path):
                _move_into_place(separated, vocals_path)
                separated = vocals_path
            audio_path = separated

        if rep.cancelled():
            return {"failed_reason": "cancelled"}

        qwen_run = transcript_mode == "whisper" and asr_backend_choice == "qwen3_asr"
        # Groq and the Qwen3-on-Whisper-segments backend transcribe a whole
        # file in one language, so only the local Whisper path switches.
        mixed_whisper_run = (mixed_languages and transcript_mode == "whisper"
                             and asr_backend_choice == "whisper" and not use_groq)
        stage_max = QWEN_SPLIT if qwen_run else RUNNING_MAX
        step_label = " (step 1 of 2)" if qwen_run else ""

        if vad_run:
            # No Whisper: the speech detector draws the boundaries and
            # Qwen3-ASR writes the text.
            if rep.cancelled():
                return {"failed_reason": "cancelled"}
            _vad_set_stage("Loading audio (CPU)")

            def _vad_progress(frac):
                vad_stage["percent"] = True
                vad_stage["ticker"].stop()
                frac = min(max(frac, 0.0), 1.0)
                if ((vad_refine_timing or asr_backend_choice == "qwen3_asr_long")
                        and not mixed_languages and frac > 0.9):
                    # The backend gives the last tenth to the forced aligner.
                    where = qwen_device.get("Qwen3 forced alignment")
                    message = ("Aligning timing with the Qwen3 forced aligner"
                               f"{f' on {where}' if where else ''}... {frac * 100:.0f}%")
                else:
                    where = qwen_device.get("Qwen3-ASR")
                    message = (f"Transcribing with Qwen3-ASR{f' on {where}' if where else ''}"
                               f"... {frac * 100:.0f}%")
                rep.progress(frac * RUNNING_MAX, message)

            import vad_segments
            try:
                segments = asr_backend.get_backend(asr_backend_choice).transcribe(
                    audio_path, source_language, use_gpu=use_gpu, batch_size=qwen_batch_size,
                    progress_cb=_vad_progress, cancel_check=rep.raise_if_cancelled,
                    refine_timing=vad_refine_timing, mixed_languages=mixed_languages,
                    stage_cb=_vad_set_stage, on_device=_on_vad_device,
                    on_gpu_fallback=_qwen_on_fallback, detector=voice_detector,
                    on_notice=detector_notices.append)
            except vad_segments.VadNotInstalledError:
                return {"failed_reason": "dependency_missing",
                        "detail": _MISSING_VAD_MESSAGE}
            except ImportError:
                return {"failed_reason": "dependency_missing",
                        "detail": _MISSING_QWEN_MESSAGE}
            except whisper_models.ModelDownloadError as exc:
                return {"failed_reason": "model_download", "detail": redact_secrets(str(exc))}
            except ValueError as exc:
                return {"failed_reason": "qwen3_asr", "detail": redact_secrets(str(exc))}
            except background_jobs.JobCancelled:
                whisper_models.release_gpu_models()
                raise
            finally:
                if vad_stage["ticker"]:
                    vad_stage["ticker"].stop()
        elif use_groq:
            rep.progress(0.0, "Transcribing via Groq's cloud API...")
            try:
                segments = core_module.transcribe_with_groq(
                    audio_path, source_language, groq_api_key,
                    progress_cb=lambda frac: rep.progress(
                        min(frac, 1.0) * stage_max,
                        f"Transcribing via Groq's cloud API{step_label}... {frac * 100:.0f}%"))
            except core_module.GroqTranscriptionError as exc:
                # Raw text stays in the in-memory result (as for vocal
                # separation and model download here); jobs_service.
                # project_result redacts "detail" before it is returned or
                # mirrored to job_records.
                return {"failed_reason": "groq", "detail": str(exc)}
        else:
            try:
                model_cached = bool(local_model_path) or whisper_models.is_whisper_model_cached(
                    whisper_size)
                # Loaded here (cached in core, so transcribe_for_timing reuses
                # it) so the download/load phase and the device actually
                # chosen are visible instead of "Starting..." for minutes.
                load_started = time.monotonic()
                with rep.stage(_model_loading_message(whisper_size, model_cached)):
                    whisper_models.load_whisper_model(whisper_size, use_gpu=use_gpu,
                                                   local_model_path=local_model_path)
                stage_seconds["load"] = time.monotonic() - load_started
                rep.raise_if_cancelled()
                device_info = whisper_models.get_whisper_device_info(
                    whisper_size, use_gpu=use_gpu, local_model_path=local_model_path)
                device_msg = whisper_models.describe_whisper_device(device_info)
                # The model fell back to CPU while loading, before any
                # inference could fail: say so in the result too.
                if device_info.get("gpu_error"):
                    gpu_fallback_msg.append(device_info["gpu_error"])
                device_suffix = f" ({device_msg})" if device_msg else ""
                rep.progress(0.0, f"Transcribing... starting; the percent appears once "
                                  f"the first lines are found{device_suffix}")

                def _whisper_progress(frac):
                    # The clock starts at the first percent: model download and
                    # load are not transcription speed.
                    if frac > 0 and "t" not in whisper_clock:
                        whisper_clock.update(t=time.monotonic(), p=min(frac, 1.0))
                        # faster-whisper decodes and runs VAD lazily before its
                        # first segment, so that wait is the best "decode and VAD" reading.
                        stage_seconds["decode_vad"] = whisper_clock["t"] - transcribe_started
                    rep.raise_if_cancelled()
                    rep.progress(min(frac, 1.0) * stage_max,
                                 f"Transcribing{step_label}... {frac * 100:.0f}%{device_suffix}")
                transcribe_started = time.monotonic()
                if mixed_whisper_run:
                    import mixed_language
                    segments = mixed_language.transcribe_mixed_whisper(
                        audio_path, source_language, whisper_size, use_gpu=use_gpu,
                        local_model_path=local_model_path, initial_prompt=initial_prompt,
                        beam_size=beam_size,
                        on_gpu_fallback=lambda exc: gpu_fallback_msg.append(
                            whisper_models.short_reason(exc)),
                        progress_cb=_whisper_progress, cancel_check=rep.raise_if_cancelled,
                        repeat_guard=repeat_guard, sensitivity_preset=sensitivity_preset)
                else:
                    segments = transcribe_for_timing(
                        audio_path, whisper_size, language=source_language, use_gpu=use_gpu,
                        local_model_path=local_model_path, hf_token=None,
                        initial_prompt=initial_prompt,
                        beam_size=beam_size,
                        min_silence_duration_ms=(asr_backend.SENTENCE_SPLIT_MIN_SILENCE_MS
                                                 if sentence_lines else min_silence_ms),
                        vad_threshold=vad_threshold,
                        on_gpu_fallback=lambda exc: gpu_fallback_msg.append(
                            whisper_models.short_reason(exc)),
                        progress_cb=_whisper_progress,
                        fast_mode=whisper_fast_mode,
                        hallucination_silence_sec=hallucination_silence_sec,
                        repeat_guard=repeat_guard, sensitivity_preset=sensitivity_preset)
                # Not recorded for the speed estimate: a per-span detection run is slower.
                if "t" in whisper_clock and not mixed_whisper_run:
                    whisper_clock["work"] = time.monotonic() - whisper_clock["t"]
                    stage_seconds["transcribe"] = whisper_clock["work"]
            except whisper_models.ModelDownloadError as exc:
                return {"failed_reason": "model_download", "detail": str(exc)}
            except background_jobs.JobCancelled:
                whisper_models.release_gpu_models()   # hand the VRAM back on a cancel too
                raise

        if not segments:
            return {"failed_reason": "empty"}

        if (realign_long_segments and not vad_run and not mixed_whisper_run
                and not rep.cancelled()):
            import word_align
            align_started = time.monotonic()
            try:
                # The ticker covers the model load and the first segment, which
                # report nothing; the first finished segment hands over to real progress.
                align_ticker = rep.stage("Splitting long merged lines",
                                         frac=stage_max * REALIGN_START).start()

                def _align_progress(done, total):
                    align_ticker.stop()
                    rep.progress(
                        stage_max * (REALIGN_START + (1 - REALIGN_START) * done / total),
                        f"Splitting long merged lines: {done} of {total}")

                try:
                    segments = word_align.realign_oversized_segments(
                        segments, audio_path, source_language, chinese_script=chinese_script,
                        progress_cb=_align_progress, cancel_check=rep.cancelled)
                finally:
                    align_ticker.stop()
            except word_align.WordAlignError as exc:
                word_align_error = str(exc)
            stage_seconds["align"] = time.monotonic() - align_started
            # The segments already split are kept in `segments`; the job as a
            # whole still ends cancelled, like a cancel in any other stage.
            if rep.cancelled():
                whisper_models.release_gpu_models()
                return {"failed_reason": "cancelled"}

        if transcript_mode == "whisper":
            raw_backend, raw_model, raw_mode = "whisper", whisper_size, "whisper"
            if vad_run:
                raw_backend, raw_model = asr_backend_choice, "Qwen3-ASR"
            elif asr_backend_choice == "qwen3_asr":
                if rep.cancelled():
                    return {"failed_reason": "cancelled"}
                # Timing stays Whisper's VAD segments; only the text is replaced
                # (asr_backend.py's module docstring explains why).
                qwen_ticker = rep.stage(
                    "Re-transcribing with Qwen3-ASR (step 2 of 2; no percent until "
                    "the first batch finishes)", frac=QWEN_SPLIT).start()

                def _qwen_progress(frac):
                    # The first finished batch ends the no-percent phase; the
                    # ticker would otherwise overwrite the message.
                    qwen_ticker.stop()
                    rep.raise_if_cancelled()
                    where = qwen_device.get("Qwen3-ASR")
                    rep.progress(
                        QWEN_SPLIT + min(max(frac, 0.0), 1.0) * (RUNNING_MAX - QWEN_SPLIT),
                        f"Re-transcribing with Qwen3-ASR (step 2 of 2)... {frac * 100:.0f}%"
                        f"{f' (on {where})' if where else ''}")

                try:
                    segments = asr_backend.Qwen3ASRBackend().transcribe(
                        audio_path, source_language, whisper_segments=segments, use_gpu=use_gpu,
                        batch_size=qwen_batch_size, progress_cb=_qwen_progress,
                        on_device=_qwen_on_device, on_gpu_fallback=_qwen_on_fallback)
                except ImportError:
                    return {"failed_reason": "dependency_missing",
                            "detail": _MISSING_QWEN_MESSAGE}
                except whisper_models.ModelDownloadError as exc:
                    return {"failed_reason": "model_download", "detail": redact_secrets(str(exc))}
                except ValueError as exc:
                    return {"failed_reason": "qwen3_asr", "detail": redact_secrets(str(exc))}
                finally:
                    qwen_ticker.stop()
                raw_backend, raw_model = "qwen3_asr", "Qwen3-ASR"
            lines = [Line(idx=i, start=seg["start"], end=seg["end"], zh=seg["text"],
                          speaker=seg.get("speaker") or None, flag=seg.get("flag"),
                          flag_note=seg.get("flag_note") or "", lang=seg.get("lang"),
                          word_timings=segment_splitting.encode_line_words(seg["text"],
                                                                     seg.get("words")))
                     for i, seg in enumerate(
                         s for s in long_line_split.split_long_segments(
                             segments, min_pause=min_pause_sec,
                             rules=asr_backend.SENTENCE_SPLIT_RULES if sentence_lines else None)
                         if s["text"].strip())]
            coverage_msg = " ".join(detector_notices + [coverage_warning(
                segments, _audio_duration_seconds(audio_path),
                qwen3_asr=raw_backend == "qwen3_asr") or ""]).strip() or None
        else:
            rep.progress(RUNNING_MAX, "Aligning transcript to audio timing...")
            user_lines = split_user_transcript(transcript_text)
            if alignment_method == "qwen3_forced_align":
                if rep.cancelled():
                    return {"failed_reason": "cancelled"}
                rep.progress(RUNNING_MAX,
                             "Aligning with Qwen3-ForcedAligner (true forced alignment)...")

                def _aligner_on_device(label):
                    qwen_device["Qwen3 forced alignment"] = label
                    rep.progress(RUNNING_MAX, "Aligning with Qwen3-ForcedAligner "
                                              f"(true forced alignment) on {label}...")
                try:
                    import forced_align
                    lines = forced_align.align_with_qwen3(
                        audio_path, user_lines, segments, language=source_language, use_gpu=use_gpu,
                        on_device=_aligner_on_device, cancel_check=rep.raise_if_cancelled,
                        on_gpu_fallback=lambda exc: _qwen_on_fallback(
                            "Qwen3 forced alignment", exc))
                except background_jobs.JobCancelled:
                    whisper_models.release_gpu_models()
                    raise
                except ImportError:
                    return {"failed_reason": "dependency_missing",
                            "detail": _MISSING_QWEN_MESSAGE}
                except whisper_models.ModelDownloadError as exc:
                    return {"failed_reason": "model_download", "detail": redact_secrets(str(exc))}
                except ValueError as exc:
                    # Fall back to the diff alignment, but say so in the
                    # result instead of hiding it.
                    forced_align_error = redact_secrets(str(exc))
                    lines = align_transcript_to_timing(user_lines, segments)
            else:
                lines = align_transcript_to_timing(user_lines, segments)
            raw_backend, raw_model, raw_mode = "whisper", whisper_size, "aligned_transcript"

    if qwen_device and not device_msg:
        device_msg = ", ".join(f"{task} on {label}" for task, label in qwen_device.items())
    whisper_models.release_gpu_models()
    return {"lines": lines, "segments": segments, "raw_backend": raw_backend,
            "raw_model": raw_model, "raw_mode": raw_mode, "audio_path": audio_path,
            "gpu_fallback_msgs": gpu_fallback_msg, "device_msg": device_msg,
            "gpu_fallback_task": fallback_tasks[0] if fallback_tasks else "Transcription",
            "whisper_clock": whisper_clock, "stage_seconds": stage_seconds,
            "word_align_error": word_align_error,
            "forced_align_error": forced_align_error, "coverage_warning": coverage_msg,
            **ollama_unload.take_notice_result(),
            "run_config": {
                "asr_backend": asr_backend_choice, "whisper_size": whisper_size,
                "local_model_path": local_model_path, "language": source_language,
                "transcript_mode": transcript_mode, "alignment_method": alignment_method,
                "min_silence_ms": min_silence_ms, "vad_threshold": vad_threshold,
                "beam_size": beam_size, "hallucination_silence_sec": hallucination_silence_sec,
                "min_pause_sec": min_pause_sec, "sensitivity_preset": sensitivity_preset,
                "whisper_fast_mode": whisper_fast_mode, "use_groq": use_groq and not vad_run,
                "whisper_repeat_guard": repeat_guard, "split_by_sentences": split_by_sentences,
                "separate_vocals_first": separate_vocals_first,
                "separation_backend": separation_backend,
                "realign_long_segments": realign_long_segments,
                "mixed_languages": mixed_languages, "vad_refine_timing": (vad_refine_timing
                                      or asr_backend_choice == "qwen3_asr_long"),
                "use_gpu": use_gpu, "initial_prompt": initial_prompt}}
