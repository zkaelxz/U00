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
import os
import shutil  # noqa: F401  (tests patch shutil.move through this module)
import tempfile
import time
import types
from typing import Optional

import asr_backend
import background_jobs
from jobs import process_worker
import core as core_module
import whisper_models
import long_line_split
import memory_headroom
import ollama_unload
import segment_splitting
import storage
from asr_backend import coverage_warning
from core import Line, align_transcript_to_timing, split_user_transcript, transcribe_for_timing
from diarize import select_device
from services import difficult_audio_service as difficult, settings_service, timing_settings_service
from services.qwen3_requirements_service import MISSING_VAD_MESSAGE as _MISSING_VAD_MESSAGE
from services.qwen3_requirements_service import import_failure_message
from services.transcribe_run_config import run_config
from services.transcribe_reporters import _ProcessReporter, _ThreadReporter  # noqa: F401 (re-exported)
from engine_backends.shared import redact_secrets

VAD_BACKENDS = ("qwen3_asr_vad", "qwen3_asr_long", "anime_whisper")


def _audio_duration_seconds(path) -> Optional[float]:
    """Best-effort audio length via ffprobe; None when it can't be read."""
    import media_inspect
    return media_inspect.duration_seconds(path, timeout=30)


# A running job never reports 100%: only completion does. When Qwen3-ASR
# re-transcribes after Whisper, Whisper's stage fills 0..QWEN_SPLIT and
# Qwen3's batches fill QWEN_SPLIT..RUNNING_MAX; otherwise Whisper's stage
# fills 0..RUNNING_MAX.
RUNNING_MAX = 0.99
# Where the long-line split starts within the transcription stage's slice
# (a fraction of that slice's end), so its per-segment progress has room to move.
REALIGN_START = 0.9
QWEN_SPLIT = 0.85


# Fixed sentences, never the ImportError text: that names a module and reads
# as a crash. The Diagnostics page is where the install button is.
MISSING_TRANSCRIPTION_MESSAGE = "Transcription isn't installed yet. Open Diagnostics to install it."
# Hard-subtitle reading is OCR, not speech recognition: saying "Transcription isn't installed"
# would send the user to install Whisper while the missing piece is an OCR package.
MISSING_OCR_MESSAGE = "Reading on-screen text needs an OCR package that isn't installed yet. Open Diagnostics to install one."


def missing_package_outcome(message: str = MISSING_TRANSCRIPTION_MESSAGE) -> dict:
    """The pipeline outcome for a run that could not start for a missing package."""
    return {"failed_reason": "dependency_missing", "detail": message}


def _transcribe_worker(audio_path, transcript_mode, transcript_text, source_language,
                       chinese_script, whisper_size, beam_size, min_silence_ms, vad_threshold,
                       separate_vocals_first, separation_backend, realign_long_segments,
                       whisper_fast_mode, use_groq, initial_prompt, use_gpu, asr_backend_choice,
                       alignment_method, local_model_path, qwen_batch_size, vad_refine_timing,
                       mixed_languages, hallucination_silence_sec, min_pause_sec, repeat_guard,
                       split_by_sentences, sensitivity_preset, voice_detector, qwen_prompt,
                       timing, checkpoint, scratch_dir, result_queue):
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
    process_worker.start_own_process_group()
    try:
        os.makedirs(scratch_dir, exist_ok=True)
        tempfile.tempdir = scratch_dir
        groq_api_key = settings_service.resolve_key("groq") if use_groq else None
        rep = _ProcessReporter(result_queue)
        lev = difficult.prepare(rep, audio_path, scratch_dir, transcript_mode,
                                asr_backend_choice, use_groq, mixed_languages, timing)
        outcome = _transcribe_pipeline(
            rep, lev.audio_path, transcript_mode, transcript_text,
            source_language, chinese_script, whisper_size, beam_size, min_silence_ms,
            vad_threshold, separate_vocals_first, separation_backend, realign_long_segments,
            whisper_fast_mode, use_groq, groq_api_key, initial_prompt, use_gpu,
            asr_backend_choice, alignment_method, local_model_path=local_model_path,
            qwen_batch_size=qwen_batch_size, vad_refine_timing=vad_refine_timing,
            mixed_languages=mixed_languages, vocals_work_dir=scratch_dir,
            hallucination_silence_sec=hallucination_silence_sec, min_pause_sec=min_pause_sec,
            repeat_guard=repeat_guard, split_by_sentences=split_by_sentences,
            sensitivity_preset=sensitivity_preset, voice_detector=voice_detector,
            qwen_prompt=qwen_prompt, timing=lev.timing, checkpoint=checkpoint)
        result_queue.put(("ok", lev.annotate(outcome)))
    except memory_headroom.HeadroomError as exc:
        # Its message is written for the user; the type name in front of it is not.
        result_queue.put(("ok", {"failed_reason": "headroom", "detail": redact_secrets(str(exc))}))
    except ImportError as exc:
        # The user sees the fixed sentence; the server log keeps which import
        # failed, since a broken install (a DLL that won't load) reads the same.
        result_queue.put(("ok", {**missing_package_outcome(),
                                 "import_error": whisper_models.short_reason(exc)}))
    except Exception as exc:
        result_queue.put(process_worker.error_item(exc))


_move_into_place = storage.move_into_place


class _PipelineRun:
    """One _transcribe_pipeline call: the options, the state the stages share
    and one method per stage. A stage method returns a failure outcome dict
    to stop the run, or None to carry on."""

    def __init__(self, rep, o):
        self.rep = rep
        self.o = o
        self.audio_path = o.audio_path
        self.tuning = (timing_settings_service.asr_tuning(o.timing) if o.timing is not None
                       else asr_backend.DEFAULT_TUNING)
        self.gpu_fallback_msg = []
        self.whisper_clock = {}
        self.stage_seconds = {}
        self.word_align_error = None
        self.forced_align_error = None
        self.coverage_msg = None
        self.stage_counts = {}
        self.detector_notices = []
        self.coarse_whisper_timing = False
        self.groq_usage = None
        self.units_recorded = 0
        self.device_msg = ""
        self.device_suffix = ""
        # The other backends, and a supplied transcript, bring their own lines.
        self.sentence_lines = (o.split_by_sentences and o.transcript_mode == "whisper"
                               and o.asr_backend_choice in ("whisper", "qwen3_asr"))
        self.vad_run = o.transcript_mode == "whisper" and o.asr_backend_choice in VAD_BACKENDS
        # Where each Qwen3 model actually loaded ("Qwen3-ASR" -> "GPU"|"CPU"), so
        # a busy CPU is told apart from a silent GPU fallback.
        self.qwen_device = {}
        self.fallback_tasks = []
        self.vad_ticker = None
        self.vad_percent = False
        self.anime_run = o.asr_backend_choice == "anime_whisper"
        self.vad_engine_task, self.vad_engine_name = (
            ("Anime-whisper", "anime-whisper") if self.anime_run else ("Qwen3-ASR", "Qwen3-ASR"))
        self.sep_ticker = None
        self.sep_device = ""
        self.segments = None
        self.lines = None
        self.raw_backend = self.raw_model = self.raw_mode = None
        self.transcribe_started = 0.0
        qwen_run = o.transcript_mode == "whisper" and o.asr_backend_choice == "qwen3_asr"
        # Groq and the Qwen3-on-Whisper-segments backend transcribe a whole
        # file in one language, so only the local Whisper path switches.
        self.mixed_whisper_run = (o.mixed_languages and o.transcript_mode == "whisper"
                                  and o.asr_backend_choice == "whisper" and not o.use_groq)
        self.stage_max = QWEN_SPLIT if qwen_run else RUNNING_MAX
        self.step_label = " (step 1 of 2)" if qwen_run else ""

    def run(self) -> dict:
        if self.o.transcript_mode == "hardsub_ocr":
            failed = self._read_hardsub_captions()
        else:
            failed = self._transcribe_audio()
        if failed:
            return failed
        if self.qwen_device and not self.device_msg:
            self.device_msg = ", ".join(f"{task} on {label}"
                                        for task, label in self.qwen_device.items())
        whisper_models.release_gpu_models()
        return self._outcome()

    def _qwen_on_fallback(self, task, exc):
        self.gpu_fallback_msg.append(whisper_models.short_reason(exc))
        self.fallback_tasks.append(task)

    def _qwen_on_device(self, task, label):
        self.qwen_device[task] = label

    def _read_hardsub_captions(self):
        o, rep = self.o, self.rep
        import hardsub_ocr
        hardsub_info = {}
        self.segments = hardsub_ocr.extract_hardsub_subtitles(
            o.video_path, language=o.source_language, sample_interval=o.hardsub_interval,
            ocr_backend=o.hardsub_ocr_backend, chinese_script=o.chinese_script,
            tesseract_cmd=o.tesseract_cmd, job_id=rep.job_id, info=hardsub_info,
            cancel_check=rep.raise_if_cancelled,
            progress_cb=lambda frac: rep.progress(
                frac, f"Reading captions from video... {frac * 100:.0f}%"))
        if not self.segments:
            return {"failed_reason": "no_text"}
        # OCR already produces real per-cue timing straight from the video --
        # no separate alignment step needed, same reasoning as the Whisper-
        # text-override branch below, just sourced from captions.
        self.lines = [Line(idx=i, start=seg["start"], end=seg["end"], zh=seg["text"])
                      for i, seg in enumerate(self.segments) if seg["text"].strip()]
        self.raw_backend, self.raw_mode = "hardsub_ocr", "hardsub_ocr"
        self.raw_model = hardsub_info.get("backend", o.hardsub_ocr_backend)
        dropped = hardsub_info.get("dropped_frames", 0)
        dropped_msg = (f"{dropped} sampled frame{'s' if dropped != 1 else ''} timed out while "
                       "reading and may be missing captions." if dropped else None)
        self.coverage_msg = " ".join(filter(None, [hardsub_info.get("note"), dropped_msg])) or None
        return None

    def _transcribe_audio(self):
        o, rep = self.o, self.rep
        if o.separate_vocals_first:
            failed = self._separate_vocals()
            if failed:
                return failed
        if rep.cancelled():
            return {"failed_reason": "cancelled"}
        if self.vad_run:
            failed = self._transcribe_with_vad_backend()
        elif o.use_groq:
            failed = self._transcribe_with_groq()
        else:
            failed = self._transcribe_with_whisper()
        if failed:
            return failed
        if self.segments is None:
            raise RuntimeError("the ASR stage finished without a segment list")

        self.stage_counts["ASR output (after voice detection and its text filters)"] = len(
            self.segments)
        if not self.segments:
            return {"failed_reason": "no_lines", "stage_summary": _stage_summary(self.stage_counts)}

        # Only a Whisper transcript carries the notice; a supplied one is aligned below.
        if self.coarse_whisper_timing and o.transcript_mode == "whisper" and not rep.cancelled():
            self.segments = _refine_without_word_timestamps(
                self.segments, self.audio_path, o.source_language, o.use_gpu, rep,
                self.qwen_device, self._qwen_on_fallback, self.detector_notices,
                self.mixed_whisper_run, self.stage_max)

        if (o.realign_long_segments and not self.vad_run and not self.mixed_whisper_run
                and not rep.cancelled()):
            failed = self._realign_long_segments()
            if failed:
                return failed

        if o.transcript_mode == "whisper":
            return self._lines_from_whisper()
        return self._lines_from_user_transcript()

    def _sep_message(self, frac):
        where = f" {self.sep_device}" if self.sep_device else ""
        return f"Separating vocals{where}, {frac * 100:.0f}%"

    def _sep_event(self, event, value):
        if self.sep_ticker:
            self.sep_ticker.stop()
            self.sep_ticker = None
        if event == "loading":
            # The model download/load cannot report progress.
            self.sep_ticker = self.rep.stage(
                "Loading the vocal separation model (downloads on first use)").start()
        elif event == "device":
            self.sep_device = _separation_device_label(value)
            self.rep.progress(0.0, self._sep_message(0.0))

    def _separate_vocals(self):
        o, rep = self.o, self.rep
        import audio_preprocess
        vocals_path = os.path.join(os.path.dirname(self.audio_path), "vocals.wav")
        rep.progress(0.0, "Separating vocals from background music...")
        separate_started = time.monotonic()
        try:
            separated = audio_preprocess.separate_vocals(
                self.audio_path, os.path.join(o.vocals_work_dir, "vocals.wav")
                if o.vocals_work_dir else vocals_path, backend=o.separation_backend,
                progress_cb=lambda frac: rep.progress(frac, self._sep_message(frac)),
                cancel_check_cb=rep.cancelled,
                use_gpu=o.use_gpu, event_cb=self._sep_event)
        except audio_preprocess.VocalSeparationCancelled:
            return {"failed_reason": "cancelled"}
        except audio_preprocess.VocalSeparationError as exc:
            return {"failed_reason": "vocal_separation", "detail": str(exc)}
        finally:
            if self.sep_ticker:
                self.sep_ticker.stop()
        self.stage_seconds["separate"] = time.monotonic() - separate_started
        if o.vocals_work_dir and os.path.abspath(separated) != os.path.abspath(vocals_path):
            _move_into_place(separated, vocals_path)
            separated = vocals_path
        self.audio_path = separated
        return None

    def _vad_set_stage(self, text):
        if self.vad_ticker:
            self.vad_ticker.stop()
        self.vad_ticker = self.rep.stage(text).start()

    def _on_vad_device(self, task, label):
        self._qwen_on_device(task, label)
        # The model load ends the loading stage; until the first percent the
        # stage text says where the work runs.
        if task == self.vad_engine_task and self.vad_ticker and not self.vad_percent:
            self._vad_set_stage(f"Transcribing with {self.vad_engine_name} on {label} "
                                "(no percent until the first batch finishes)")

    def _vad_progress(self, frac):
        o = self.o
        self.vad_percent = True
        if self.vad_ticker is None:
            raise RuntimeError("Qwen3-ASR reported progress before its stage started")
        self.vad_ticker.stop()
        frac = min(max(frac, 0.0), 1.0)
        if ((o.vad_refine_timing or o.asr_backend_choice == "qwen3_asr_long")
                and not o.mixed_languages and not self.anime_run and frac > 0.9):
            # The backend gives the last tenth to the forced aligner.
            where = self.qwen_device.get("Qwen3 forced alignment")
            message = ("Aligning timing with the Qwen3 forced aligner"
                       f"{f' on {where}' if where else ''}... {frac * 100:.0f}%")
        else:
            where = self.qwen_device.get(self.vad_engine_task)
            message = (f"Transcribing with {self.vad_engine_name}"
                       f"{f' on {where}' if where else ''}... {frac * 100:.0f}%")
        self.rep.progress(frac * RUNNING_MAX, message)

    def _transcribe_with_vad_backend(self):
        """No Whisper: the speech detector draws the boundaries and Qwen3-ASR
        writes the text."""
        o, rep = self.o, self.rep
        if rep.cancelled():
            return {"failed_reason": "cancelled"}
        self._vad_set_stage("Loading audio (CPU)")
        import vad_segments
        try:
            self.segments = asr_backend.get_backend(o.asr_backend_choice).transcribe(
                self.audio_path, o.source_language, use_gpu=o.use_gpu, batch_size=o.qwen_batch_size,
                progress_cb=self._vad_progress, cancel_check=rep.raise_if_cancelled,
                refine_timing=o.vad_refine_timing, mixed_languages=o.mixed_languages,
                stage_cb=self._vad_set_stage, on_device=self._on_vad_device,
                on_gpu_fallback=self._qwen_on_fallback, detector=o.voice_detector,
                on_notice=self.detector_notices.append, prompt=o.qwen_prompt, tuning=self.tuning,
                # Only anime-whisper takes it; the Qwen3 backends have no decode penalties.
                **({"repeat_guard": o.repeat_guard} if self.anime_run else {}))
        except vad_segments.VadNotInstalledError:
            return {"failed_reason": "dependency_missing",
                    "detail": _MISSING_VAD_MESSAGE}
        except ImportError as exc:
            return missing_package_outcome(import_failure_message(exc))
        except whisper_models.ModelDownloadError as exc:
            return {"failed_reason": "model_download", "detail": redact_secrets(str(exc))}
        except ValueError as exc:
            return {"failed_reason": "anime_whisper" if self.anime_run else "qwen3_asr",
                    "detail": redact_secrets(str(exc))}
        except background_jobs.JobCancelled:
            whisper_models.release_gpu_models()
            raise
        finally:
            if self.vad_ticker:
                self.vad_ticker.stop()
        return None

    def _transcribe_with_groq(self):
        o, rep = self.o, self.rep
        rep.progress(0.0, "Transcribing via Groq's cloud API...")
        # Billed by audio length; the parent writes the usage row (a worker
        # process must not read the database).
        self.groq_usage = {"seconds": _audio_duration_seconds(self.audio_path) or 0.0,
                           "failed": True}
        try:
            self.segments = core_module.transcribe_with_groq(
                self.audio_path, o.source_language, o.groq_api_key,
                progress_cb=lambda frac: rep.progress(
                    min(frac, 1.0) * self.stage_max,
                    f"Transcribing via Groq's cloud API{self.step_label}... {frac * 100:.0f}%"))
        except core_module.GroqTranscriptionError as exc:
            # Raw text stays in the in-memory result (as for vocal
            # separation and model download here); jobs_service.
            # project_result redacts "detail" before it is returned or
            # mirrored to job_records.
            return {"failed_reason": "groq", "detail": str(exc), "groq_usage": self.groq_usage}
        self.groq_usage["failed"] = False
        return None

    def _transcribe_with_whisper(self):
        o = self.o
        try:
            self._load_whisper_model()
            self.transcribe_started = time.monotonic()
            self.segments = (self._decode_mixed_languages() if self.mixed_whisper_run
                             else self._decode_whisper())
            self.coarse_whisper_timing = not whisper_models.supports_word_timestamps(o.whisper_size)
            # Not recorded for the speed estimate: a per-span detection run is slower.
            if "t" in self.whisper_clock and not self.mixed_whisper_run:
                self.whisper_clock["work"] = time.monotonic() - self.whisper_clock["t"]
                self.stage_seconds["transcribe"] = self.whisper_clock["work"]
        except whisper_models.ModelDownloadError as exc:
            return {"failed_reason": "model_download", "detail": str(exc)}
        except background_jobs.JobCancelled:
            whisper_models.release_gpu_models()   # hand the VRAM back on a cancel too
            raise
        return None

    def _load_whisper_model(self):
        o, rep = self.o, self.rep
        model_cached = bool(o.local_model_path) or whisper_models.is_whisper_model_cached(
            o.whisper_size)
        # Loaded here (cached in core, so transcribe_for_timing reuses
        # it) so the download/load phase and the device actually
        # chosen are visible instead of "Starting..." for minutes.
        load_started = time.monotonic()
        with rep.stage(_model_loading_message(o.whisper_size, model_cached)):
            whisper_models.load_whisper_model(o.whisper_size, use_gpu=o.use_gpu,
                                              local_model_path=o.local_model_path)
        self.stage_seconds["load"] = time.monotonic() - load_started
        rep.raise_if_cancelled()
        device_info = whisper_models.get_whisper_device_info(
            o.whisper_size, use_gpu=o.use_gpu, local_model_path=o.local_model_path)
        self.device_msg = whisper_models.describe_whisper_device(device_info)
        # The model fell back to CPU while loading, before any
        # inference could fail: say so in the result too.
        if device_info.get("gpu_error"):
            self.gpu_fallback_msg.append(device_info["gpu_error"])
        self.device_suffix = f" ({self.device_msg})" if self.device_msg else ""
        rep.progress(0.0, f"Transcribing... starting; the percent appears once "
                          f"the first lines are found{self.device_suffix}")

    def _whisper_progress(self, frac):
        # The clock starts at the first percent: model download and
        # load are not transcription speed.
        if frac > 0 and "t" not in self.whisper_clock:
            self.whisper_clock.update(t=time.monotonic(), p=min(frac, 1.0))
            # faster-whisper decodes and runs VAD lazily before its
            # first segment, so that wait is the best "decode and VAD" reading.
            self.stage_seconds["decode_vad"] = self.whisper_clock["t"] - self.transcribe_started
        self.rep.raise_if_cancelled()
        self.rep.progress(min(frac, 1.0) * self.stage_max,
                          f"Transcribing{self.step_label}... {frac * 100:.0f}%{self.device_suffix}")

    def _on_whisper_gpu_fallback(self, exc):
        self.gpu_fallback_msg.append(whisper_models.short_reason(exc))

    def _decode_mixed_languages(self):
        o = self.o
        import mixed_language
        return mixed_language.transcribe_mixed_whisper(
            self.audio_path, o.source_language, o.whisper_size, use_gpu=o.use_gpu,
            local_model_path=o.local_model_path, initial_prompt=o.initial_prompt,
            beam_size=o.beam_size, on_gpu_fallback=self._on_whisper_gpu_fallback,
            progress_cb=self._whisper_progress, cancel_check=self.rep.raise_if_cancelled,
            repeat_guard=o.repeat_guard, sensitivity_preset=o.sensitivity_preset)

    def _record_unit(self, segments):
        # Unit ids continue after the resumed ones; the loader refuses a set that doubles back.
        cp = self.o.checkpoint
        self.rep.checkpoint_unit(cp["scope"], cp["resumed_units"] + self.units_recorded, segments)
        self.units_recorded += 1

    def _decode_whisper(self):
        o, tuning = self.o, self.tuning
        cp = o.checkpoint
        return transcribe_for_timing(
            self.audio_path, o.whisper_size, language=o.source_language, use_gpu=o.use_gpu,
            local_model_path=o.local_model_path, hf_token=None,
            initial_prompt=o.initial_prompt, chinese_script=o.chinese_script,
            beam_size=o.beam_size,
            min_silence_duration_ms=(tuning.sentence_min_silence_ms
                                     if self.sentence_lines else o.min_silence_ms),
            vad_threshold=o.vad_threshold,
            on_gpu_fallback=self._on_whisper_gpu_fallback,
            progress_cb=self._whisper_progress, on_notice=self.detector_notices.append,
            fast_mode=o.whisper_fast_mode,
            hallucination_silence_sec=o.hallucination_silence_sec,
            repeat_guard=o.repeat_guard, sensitivity_preset=o.sensitivity_preset,
            filter_hallucination_repeats=tuning.repeat_min_repeats,
            stock_isolation_sec=tuning.stock_isolation_sec,
            resume_segments=cp and cp["segments"], on_unit=(cp and cp["record"] and self._record_unit) or None,
            **difficult.whisper_kwargs(o.timing))

    def _realign_long_segments(self):
        o, rep, tuning = self.o, self.rep, self.tuning
        import word_align
        align_started = time.monotonic()
        try:
            # The ticker covers the model load and the first segment, which
            # report nothing; the first finished segment hands over to real progress.
            align_ticker = rep.stage("Splitting long merged lines",
                                     frac=self.stage_max * REALIGN_START).start()

            def _align_progress(done, total):
                align_ticker.stop()
                rep.progress(
                    self.stage_max * (REALIGN_START + (1 - REALIGN_START) * done / total),
                    f"Splitting long merged lines: {done} of {total}")

            try:
                self.segments = word_align.realign_oversized_segments(
                    self.segments, self.audio_path, o.source_language,
                    chinese_script=o.chinese_script,
                    # The job already holds a GPU slot (gpu_touching); use_gpu is the
                    # user's choice, so a CPU-only run never touches CUDA.
                    device=select_device(o.use_gpu),
                    min_duration_to_realign=tuning.realign_min_seconds,
                    min_pause_seconds=tuning.realign_pause_seconds,
                    progress_cb=_align_progress, cancel_check=rep.cancelled)
            finally:
                align_ticker.stop()
        except word_align.WordAlignError as exc:
            self.word_align_error = str(exc)
        self.stage_seconds["align"] = time.monotonic() - align_started
        # The segments already split are kept in `segments`; the job as a
        # whole still ends cancelled, like a cancel in any other stage.
        if rep.cancelled():
            whisper_models.release_gpu_models()
            return {"failed_reason": "cancelled"}
        return None

    def _lines_from_whisper(self):
        o, rep = self.o, self.rep
        self.raw_backend, self.raw_model, self.raw_mode = "whisper", o.whisper_size, "whisper"
        if self.vad_run:
            self.raw_backend, self.raw_model = o.asr_backend_choice, self.vad_engine_task
        elif o.asr_backend_choice == "qwen3_asr":
            if rep.cancelled():
                return {"failed_reason": "cancelled"}
            failed = self._retranscribe_with_qwen()
            if failed:
                return failed
            self.raw_backend, self.raw_model = "qwen3_asr", "Qwen3-ASR"
        duration = _audio_duration_seconds(self.audio_path)
        self.segments = segment_splitting.sanitize_segment_times(self.segments or [], duration)
        self.lines = self._split_into_lines()
        self.stage_counts["lines after splitting long segments"] = len(self.lines)
        self.coverage_msg = " ".join(self.detector_notices + [coverage_warning(
            self.segments, duration,
            qwen3_asr=self.raw_backend == "qwen3_asr") or ""]).strip() or None
        return None

    def _retranscribe_with_qwen(self):
        """Timing stays Whisper's VAD segments; only the text is replaced
        (asr_backend.py's module docstring explains why)."""
        o, rep = self.o, self.rep
        qwen_ticker = rep.stage(
            "Re-transcribing with Qwen3-ASR (step 2 of 2; no percent until "
            "the first batch finishes)", frac=QWEN_SPLIT).start()

        def _qwen_progress(frac):
            # The first finished batch ends the no-percent phase; the
            # ticker would otherwise overwrite the message.
            qwen_ticker.stop()
            rep.raise_if_cancelled()
            where = self.qwen_device.get("Qwen3-ASR")
            rep.progress(
                QWEN_SPLIT + min(max(frac, 0.0), 1.0) * (RUNNING_MAX - QWEN_SPLIT),
                f"Re-transcribing with Qwen3-ASR (step 2 of 2)... {frac * 100:.0f}%"
                f"{f' (on {where})' if where else ''}")

        try:
            self.segments = asr_backend.Qwen3ASRBackend().transcribe(
                self.audio_path, o.source_language, whisper_segments=self.segments,
                use_gpu=o.use_gpu, batch_size=o.qwen_batch_size, progress_cb=_qwen_progress,
                on_device=self._qwen_on_device, on_gpu_fallback=self._qwen_on_fallback,
                prompt=o.qwen_prompt)
        except ImportError as exc:
            return missing_package_outcome(import_failure_message(exc))
        except whisper_models.ModelDownloadError as exc:
            return {"failed_reason": "model_download", "detail": redact_secrets(str(exc))}
        except ValueError as exc:
            return {"failed_reason": "qwen3_asr", "detail": redact_secrets(str(exc))}
        finally:
            qwen_ticker.stop()
        return None

    def _split_into_lines(self):
        o, tuning = self.o, self.tuning
        return [Line(idx=i, start=seg["start"], end=seg["end"], zh=seg["text"],
                     speaker=seg.get("speaker") or None, flag=seg.get("flag"),
                     flag_note=seg.get("flag_note") or "", lang=seg.get("lang"),
                     word_timings=segment_splitting.encode_line_words(seg["text"],
                                                                seg.get("words")))
                for i, seg in enumerate(
                    s for s in long_line_split.split_long_segments(
                        self.segments, min_pause=o.min_pause_sec,
                        max_seconds=tuning.max_seconds, max_cjk_chars=tuning.max_cjk_chars,
                        floors=tuning.floors,
                        rules=asr_backend.sentence_split_rules(tuning) if self.sentence_lines else None)
                    if s["text"].strip())]

    def _lines_from_user_transcript(self):
        o, rep = self.o, self.rep
        rep.progress(RUNNING_MAX, "Aligning transcript to audio timing...")
        user_lines = split_user_transcript(o.transcript_text)
        if o.alignment_method == "qwen3_forced_align":
            if rep.cancelled():
                return {"failed_reason": "cancelled"}
            failed = self._force_align(user_lines)
            if failed:
                return failed
        else:
            self.lines = align_transcript_to_timing(user_lines, self.segments)
        self.raw_backend, self.raw_model, self.raw_mode = (
            "whisper", o.whisper_size, "aligned_transcript")
        return None

    def _force_align(self, user_lines):
        o, rep = self.o, self.rep
        rep.progress(RUNNING_MAX,
                     "Aligning with Qwen3-ForcedAligner (true forced alignment)...")

        def _aligner_on_device(label):
            self.qwen_device["Qwen3 forced alignment"] = label
            rep.progress(RUNNING_MAX, "Aligning with Qwen3-ForcedAligner "
                                      f"(true forced alignment) on {label}...")
        try:
            import forced_align
            self.lines = forced_align.align_with_qwen3(
                self.audio_path, user_lines, self.segments, language=o.source_language,
                use_gpu=o.use_gpu, on_device=_aligner_on_device,
                cancel_check=rep.raise_if_cancelled,
                on_gpu_fallback=lambda exc: self._qwen_on_fallback("Qwen3 forced alignment", exc))
        except background_jobs.JobCancelled:
            whisper_models.release_gpu_models()
            raise
        except ImportError as exc:
            return missing_package_outcome(import_failure_message(exc))
        except whisper_models.ModelDownloadError as exc:
            return {"failed_reason": "model_download", "detail": redact_secrets(str(exc))}
        except ValueError as exc:
            # Fall back to the diff alignment, but say so in the
            # result instead of hiding it.
            self.forced_align_error = redact_secrets(str(exc))
            self.lines = align_transcript_to_timing(user_lines, self.segments)
        return None

    def _outcome(self) -> dict:
        o = self.o
        return {"lines": self.lines, "segments": self.segments, "raw_backend": self.raw_backend,
                "raw_model": self.raw_model, "raw_mode": self.raw_mode,
                "audio_path": self.audio_path,
                "gpu_fallback_msgs": self.gpu_fallback_msg, "device_msg": self.device_msg,
                "gpu_fallback_task": self.fallback_tasks[0] if self.fallback_tasks else "Transcription",
                "whisper_clock": self.whisper_clock, "stage_seconds": self.stage_seconds,
                "word_align_error": self.word_align_error,
                "forced_align_error": self.forced_align_error,
                "coverage_warning": self.coverage_msg,
                "stage_summary": _stage_summary(self.stage_counts),
                **({"groq_usage": self.groq_usage} if self.groq_usage else {}),
                **ollama_unload.take_notice_result(),
                "run_config": run_config(o, self.vad_run)}


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
                         voice_detector="standard", qwen_prompt=None, timing=None,
                         checkpoint=None) -> dict:
    """Runs ASR (or hardsub OCR, thread jobs only) and returns a plain dict:
    {"failed_reason", ...} when nothing should be applied, else the lines
    and everything _apply_transcription needs. Touches no database row.
    `rep` is a _ThreadReporter or _ProcessReporter.

    local_model_path is Settings > Offline Whisper model folder (a folder
    holding an already-downloaded faster-whisper model, used instead of a
    Hugging Face download); qwen_batch_size is the saved Qwen3-ASR batch
    size; mixed_languages is the saved per-span language detection option (Whisper and
    "qwen3_asr_vad" backends only: other backends and Groq ignore it); vad_refine_timing is the saved forced-aligner timing option of the
    "qwen3_asr_vad" backend. vocals_work_dir: where vocal separation writes before its result
    is moved next to the audio, so a killed worker leaves no partial file.
    split_by_sentences: see docs/engine-backends.md. qwen_prompt: the
    "Vocabulary: ..." hint (vocabulary_hint_service) for the Qwen3 backends, or None.
    timing: timing_settings_service.values() read by the caller (a worker process must
    not read the database), or None for the built-in thresholds.
    checkpoint: None, or {"scope", "segments", "resumed_units", "record"} (transcribe_checkpoint_service):
    the plain Whisper decode continues after `segments`, those of an earlier run over the same
    input and settings, and when `record` is set reports each finished unit of its own through the
    reporter.

    asr_backend_choice / alignment_method are the drama's stored
    choices: "qwen3_asr" only applies in whisper transcript_mode, and
    "qwen3_forced_align" only in have_transcript mode. Import/download/other Qwen3 failures end the job
    with a failed_reason ("dependency_missing", "model_download",
    "qwen3_asr"); a forced-align ValueError (e.g. an oversized line) falls
    back to the diff alignment and is reported as forced_align_error."""
    # Everything the stages read is an argument; the class gets them by name
    # so adding an option stays a one-line change here.
    return _PipelineRun(rep, types.SimpleNamespace(**locals())).run()


# Defined in the modules below; re-exported so `services.transcribe_pipeline.<name>` keeps working.
from services.transcribe_pipeline_helpers import (  # noqa: E402,F401
    _MODEL_DOWNLOAD_SIZES, _refine_without_word_timestamps, _separation_device_label,
    _stage_summary, _model_loading_message, _remove_scratch_dir)
