"""A one-shot Whisper call a job can stop waiting for.

faster-whisper has no cancel point, and a stuck model load, download or CUDA
call would otherwise hold the job's GPU slot forever. The call runs on a
helper thread that is abandoned on cancel or timeout (live_translate.run_abortable),
so the abandoned call may keep VRAM until it returns."""

import contextlib
import os

import background_jobs
import core
import live_translate
import ollama_unload

# A cached model loads in seconds and runs well faster than real time even on
# CPU, so this is generous; a model that still has to be downloaded gets far
# longer (a large model is gigabytes).
_BASE_S = 180
_DOWNLOAD_EXTRA_S = 1800
_PER_AUDIO_S = 10


def timeout_s(window_seconds: float, model_cached: bool) -> float:
    extra = 0 if model_cached else _DOWNLOAD_EXTRA_S
    return _BASE_S + extra + _PER_AUDIO_S * max(0.0, float(window_seconds))


def transcribe_bounded(job_id: str, audio_path: str, model_size: str, window_seconds: float,
                       name: str, **transcribe_kwargs):
    """Returns (segments, ollama_notice) from core.transcribe_for_timing.
    Raises background_jobs.JobCancelled once the job's cancel is requested,
    TimeoutError past timeout_s, and whatever the call itself raises. The
    audio file is removed when the call ends, abandoned or not: it stays open
    for an abandoned call, and Windows refuses to delete an open file."""
    notice = {}

    def call():
        try:
            found = core.transcribe_for_timing(audio_path, model_size, **transcribe_kwargs)
            # Kept per thread, so it is taken on the thread that ran the load.
            notice.update(ollama_unload.take_notice_result())
            return found
        finally:
            with contextlib.suppress(OSError):
                os.remove(audio_path)

    segments = live_translate.run_abortable(
        call, lambda: background_jobs.is_cancel_requested(job_id),
        timeout=timeout_s(window_seconds, core.is_whisper_model_cached(model_size)), name=name)
    return segments, notice
