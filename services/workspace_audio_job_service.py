"""
services/workspace_audio_job_service.py -- the background-job runners for
the audio-side Workspace actions: SenseVoice audio tags.

Thread-safe in the same way as `workspace_job_service`: plain Python
objects, `background_jobs` and the database only.
"""

import background_jobs
import whisper_models
from services.workspace_job_service import _raise_if_cancelled


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

