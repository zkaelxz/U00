"""
services/diarization_service.py -- Diarize-stage services for one drama,
shared by the FastAPI /api/diarization routes (a later integration step,
not this file) and the Streamlit Diarize tab (`tabs/workspace_tab.py`'s
`with tab_diarize:` block, lines ~2411-2443, and `_render_speaker_rerun`,
lines ~1068-1110).

Migration Slice 16 (Phase 6's second Workspace stage). This re-detects
speakers from the drama's already-stored audio -- it never touches
transcript text or timing (see diarize.merge_speakers's own docstring).
The "run diarization during alignment" checkbox and the actual
turns-to-lines merge are a future Transcript/Align-stage slice's
concern, not this one.

No Streamlit or FastAPI import: plain functions, plain dicts in, plain
values out, so a CLI or another service could call them too. The HF
token itself is never returned (D2 -- server-side keys are never
exposed over an API), only whether one is configured.
"""
import os
from typing import Optional

import background_jobs
import db
import diarize
from services import settings_service
from services.service_errors import (DependencyUnavailableError, NotFoundError,
                                      UnsupportedOperationError)


def _drama_audio_path(drama_id: int, drama: dict) -> Optional[str]:
    """The on-disk path to this drama's stored audio, or None if there's
    no audio_filename set or the file isn't actually there -- mirrors
    tabs/workspace_tab.py's own `_speaker_audio` check at line ~2434."""
    audio_filename = drama.get("audio_filename")
    if not audio_filename:
        return None
    path = os.path.join(db.drama_dir(drama_id), audio_filename)
    return path if os.path.exists(path) else None


def get_diarization_config(drama_id: int) -> dict:
    """Read-only Diarize-stage summary for one drama: whether an HF token
    is configured, the expected-speaker-count default from the last real
    detection run (diarize.load_last_speaker_count), and whether audio is
    actually available to diarize. Raises NotFoundError for an unknown
    drama id."""
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")

    ddir = db.drama_dir(drama_id)
    audio_path = _drama_audio_path(drama_id, drama)

    return {
        "drama_id": drama_id,
        "hf_token_configured": bool(settings_service.resolve_key("hf_token")),
        "expected_speakers": diarize.load_last_speaker_count(ddir),
        "audio_available": audio_path is not None,
    }


def start_diarization_run(drama_id: int, expected_speakers: Optional[int] = None) -> dict:
    """Starts a real background job to re-detect speakers from this
    drama's stored audio -- the same action as the Diarize tab's own
    "Re-run speaker detection" button (_render_speaker_rerun). The
    transcript text/timing are never touched; only merging the resulting
    turns back onto lines is out of scope here (a later slice). Raises
    NotFoundError for an unknown drama id or if no audio is available,
    DependencyUnavailableError if no Hugging Face token is configured.
    Returns {"job_id": ...} -- poll it via the existing GET /api/jobs/
    {job_id}."""
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")

    hf_token = settings_service.resolve_key("hf_token")
    if not hf_token:
        raise DependencyUnavailableError(
            "No Hugging Face token is configured. Set one in Settings first.")

    audio_path = _drama_audio_path(drama_id, drama)
    if audio_path is None:
        # The drama itself exists (checked above) -- it just has no audio
        # yet, which is a well-formed request this record can't currently
        # satisfy (HTTP 400), not "the drama doesn't exist" (404).
        raise UnsupportedOperationError(f"No audio available for drama {drama_id}.")

    job_id = f"diarize_{drama_id}"
    background_jobs.start_process_job(
        job_id, diarize.diarize_subprocess_worker,
        args=(audio_path, hf_token, expected_speakers or None),
        gpu_touching=True, description=f"Diarization (drama #{drama_id})")
    return {"job_id": job_id}
