"""
services/workspace_audio_job_service.py -- the background-job runners for
the audio-side Workspace actions: Whisper transcription, hard-sub OCR and
SenseVoice audio tags.

Thread-safe in the same way as `workspace_job_service`: plain Python
objects, `background_jobs` and the database only.
"""

import os

import background_jobs
import ollama_unload
import whisper_transcribe
import whisper_models
from whisper_transcribe import transcribe_for_timing
from services.workspace_job_service import _raise_if_cancelled


def run_transcribe_job(job_id, audio_path, whisper_size, language, use_gpu,
                        local_model_path, hf_token, initial_prompt, beam_size,
                        min_silence_duration_ms, vad_threshold=0.5, separate_vocals_first=False,
                        realign_long_segments=False, chinese_script="simplified", fast_mode=False,
                        separation_backend="auto", use_groq=False, groq_api_key=None):
    """
    Runs just the Whisper speech-recognition pass in a background thread,
    same reasoning as run_translate_job above: this is the step that
    dominates wall-clock time on a long-form file (3+ hours), so it's the
    one worth reporting progress on and not blocking the rest of the app
    for. The Qwen3-ASR text override, alignment, and diarization that can
    follow it are not part of this function (services/transcribe_service
    runs the full pipeline).

    separate_vocals_first: runs audio_preprocess.separate_vocals() on
    audio_path before transcribing, writing the vocals-only result
    alongside the original audio (as "vocals.wav" in the same folder) and
    transcribing THAT instead -- for content with a music bed under the
    dialogue, which Whisper otherwise has to fight through. Adds real
    processing time (a full Demucs pass over the whole file); a missing
    Demucs install or a separation failure is recorded the same way as a
    Whisper model-download failure below, not raised, since it's an
    expected/common outcome (an optional dependency the person hasn't
    installed) rather than a bug.

    use_groq: sends audio_path to Groq's hosted cloud Whisper
    API (core.transcribe_with_groq) instead of running local Whisper at
    all. whisper_size/beam_size/vad_threshold/fast_mode have no effect
    on this path -- Groq's own hosted model and its own VAD produce
    segments directly. A failure here (bad key, network, rate limit) is
    recorded via failed_reason="groq", the same "expected outcome, not a
    bug" treatment as a local model-download failure below.

    realign_long_segments: EXPERIMENTAL, off by default -- runs
    word_align.realign_oversized_segments() on the transcript afterward,
    splitting any oversized VAD-merged segment back into multiple
    correctly-timed lines using real word-level alignment (see
    word_align.py's own docstring for why MMS, and the real documented
    CJK failure mode it fails soft against). A missing torchaudio/uroman
    install is recorded the same way as a missing Demucs install above;
    a per-line alignment problem is NOT surfaced here at all, since
    word_align.py already degrades those individually and silently back
    to their original timing rather than raising.

    Model-download failure and "no audio detected" are expected, common
    outcomes here (a flaky connection, a silent/corrupt file), not bugs --
    both are recorded via a "failed_reason" on the result instead of
    raising, so the main script can show the same specific, actionable
    messages it always has instead of a generic error+traceback. Anything
    else that goes wrong is a real bug and is left to raise, same as
    run_translate_job -- background_jobs.start_job's own runner catches
    that and reports it as a job error.
    """
    if separate_vocals_first:
        import audio_preprocess
        background_jobs.update_progress(job_id, 0.0, "Separating vocals from background music...")
        vocals_path = os.path.join(os.path.dirname(audio_path), "vocals.wav")
        try:
            audio_path = audio_preprocess.separate_vocals(
                audio_path, vocals_path, backend=separation_backend,
                progress_cb=lambda frac: background_jobs.update_progress(
                    job_id, frac, f"Removing background music... {frac * 100:.0f}%"),
                cancel_check_cb=lambda: background_jobs.is_cancel_requested(job_id))
        except audio_preprocess.VocalSeparationCancelled:
            background_jobs.set_result(job_id, {"failed_reason": "cancelled"})
            return
        except audio_preprocess.VocalSeparationError as exc:
            background_jobs.set_result(job_id, {"failed_reason": "vocal_separation", "detail": str(exc)})
            return

    # separate_vocals()'s own cancel_check_cb only fires between
    # its internal chunks, so a cancel requested right at its tail (or,
    # when separation is off/skipped, a cancel requested before this
    # point is even reached) fell through this checkpoint-free gap and
    # let the expensive Whisper pass start anyway, uninterrupted, on
    # short files where separation has few or no chunk boundaries.
    if background_jobs.is_cancel_requested(job_id):
        background_jobs.set_result(job_id, {"failed_reason": "cancelled"})
        return

    gpu_fallback_msg = []
    if use_groq:
        background_jobs.update_progress(job_id, 0.0, "Transcribing via Groq's cloud API...")
        try:
            segments = whisper_transcribe.transcribe_with_groq(
                audio_path, language, groq_api_key,
                progress_cb=lambda frac: background_jobs.update_progress(
                    job_id, frac, f"Transcribing via Groq's cloud API... {frac * 100:.0f}%"))
        except whisper_transcribe.GroqTranscriptionError as exc:
            background_jobs.set_result(job_id, {"failed_reason": "groq", "detail": str(exc)})
            return
    else:
        try:
            segments = transcribe_for_timing(
                audio_path, whisper_size, language=language, use_gpu=use_gpu,
                local_model_path=local_model_path, hf_token=hf_token,
                initial_prompt=initial_prompt, beam_size=beam_size,
                min_silence_duration_ms=min_silence_duration_ms, vad_threshold=vad_threshold,
                on_gpu_fallback=lambda exc: gpu_fallback_msg.append(whisper_models.short_reason(exc)),
                progress_cb=lambda frac: background_jobs.update_progress(
                    job_id, frac, f"Transcribing... {frac * 100:.0f}%"),
                fast_mode=fast_mode)
        except whisper_models.ModelDownloadError as exc:
            background_jobs.set_result(job_id, {"failed_reason": "model_download", "detail": str(exc)})
            return

    if not segments:
        background_jobs.set_result(job_id, {"failed_reason": "empty"})
        return

    # Whisper's own pass has no cancel checkpoint of its own yet -- this is the next point a
    # cancel requested mid-transcription can actually be honored. The
    # expensive work is already done by here, so a cancel caught this
    # late just skips the optional realign step rather than discarding
    # the transcript -- never lose already-done work over a cancel, same
    # rule this app follows for an optional add-on failing outright.
    word_align_error = None
    if realign_long_segments and not background_jobs.is_cancel_requested(job_id):
        import word_align
        background_jobs.update_progress(job_id, 1.0, "Splitting long merged lines...")
        try:
            segments = word_align.realign_oversized_segments(
                segments, audio_path, language, chinese_script=chinese_script,
                progress_cb=lambda done, total: background_jobs.update_progress(
                    job_id, 1.0, f"Splitting long merged lines: {done} of {total}"),
                cancel_check=lambda: background_jobs.is_cancel_requested(job_id))
        except word_align.WordAlignError as exc:
            # A missing dependency here must never cost the transcription
            # itself (the expensive part, already done) -- proceed with
            # the unmodified segments and just report the issue, the same
            # "never lose already-done work over an optional add-on
            # failing" rule this app follows everywhere else.
            word_align_error = str(exc)

    whisper_models.release_gpu_models()  # transcription stage done
    result = {
        "segments": segments,
        "gpu_fallback": gpu_fallback_msg[0] if gpu_fallback_msg else None,
        "word_align_error": word_align_error,
        **ollama_unload.take_notice_result(),
    }
    if gpu_fallback_msg:
        result["device_notice"] = whisper_models.gpu_fallback_notice("Transcription", gpu_fallback_msg[0])
    background_jobs.set_result(job_id, result)


def run_hardsub_ocr_job(job_id, video_path, language, sample_interval, ocr_backend,
                         chinese_script="simplified", tesseract_cmd=None):
    """
    Runs hardsub_ocr's sample-frames -> auto-detect-caption-band -> OCR ->
    dedupe pipeline in a background thread. Same reasoning as
    run_transcribe_job above: OCR-ing every sampled frame of a long video
    is slow, and it needs to report real progress and not block the rest
    of the app while it runs. The result already carries real per-cue
    timing from the OCR pass itself, so unlike a raw Whisper transcript it
    needs no separate alignment step -- the polling code on the other end
    builds Lines directly from it, the same way Whisper's own
    speech-to-text override (rather than an aligned user transcript) does.
    """
    import hardsub_ocr
    cues = hardsub_ocr.extract_hardsub_subtitles(
        video_path, language=language, sample_interval=sample_interval,
        ocr_backend=ocr_backend, chinese_script=chinese_script, tesseract_cmd=tesseract_cmd,
        job_id=job_id, cancel_check=lambda: _raise_if_cancelled(job_id),
        progress_cb=lambda frac: background_jobs.update_progress(
            job_id, frac, f"Reading captions from video... {frac * 100:.0f}%"))
    if not cues:
        background_jobs.set_result(job_id, {"failed_reason": "empty"})
        return
    background_jobs.set_result(job_id, {"segments": cues})


def run_sensevoice_job(job_id, drama_id, lines, audio_path, drama_dir, use_gpu):
    """SenseVoice audio emotion/event tags per line, in the background --
    stored in their own file (sensevoice_tags.AUDIO_TAGS_FILE), never
    merged into the text-based emotions that feed translation."""
    import sensevoice_tags
    try:
        tags = sensevoice_tags.tag_lines(
            audio_path, lines, use_gpu=use_gpu,
            cancel_check=lambda: _raise_if_cancelled(job_id),
            progress_cb=lambda frac: background_jobs.update_progress(
                job_id, frac, f"Listening for emotion and sounds... {frac * 100:.0f}%"))
    finally:
        whisper_models.release_gpu_models()
    sensevoice_tags.save_audio_tags(drama_dir, tags)
    background_jobs.set_result(job_id, {"tagged": len(tags)})

