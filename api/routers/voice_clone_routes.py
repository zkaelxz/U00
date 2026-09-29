"""
api/routers/voice_clone_routes.py -- voice-clone setup for a drama's
characters (parity audit blocker #7; inventory C01, C03, C09, C13). All
logic is in services/voice_clone_service.py.

Speaker labels travel in JSON bodies (or a multipart form field), never in
path segments. Candidate clips are addressed by an opaque id.

Permissions (docs/remote-access-decision.md):
  - upload / remove a reference clip: local_only() (uploads and deletes
    are PC-only);
  - start an extraction: jobs.start (local ffmpeg only, no paid engine);
  - list candidates: lines.read (they carry the matched line's text);
  - preview a candidate: media.stream (audio bytes);
  - choose a candidate: lines.edit. It writes a file, but no client bytes:
    it copies a clip cut from the drama's own audio, like the existing
    voice-bank apply (lines.edit), and sets one speaker's fields. It never
    deletes the previous clip (deletes are PC-only);
  - save to the voice bank: admin.library (a library catalogue write,
    like the voice-bank rename);
  - series-character link: lines.edit (like the other character fields).
"""

from typing import Optional

from fastapi import APIRouter, File, Form, Path, UploadFile
from fastapi.responses import FileResponse

from api.auth import local_only, require_permission
from api.schemas import (CharactersEntry, CharactersVoiceBankEntry, ErrorResponse,
                         VoiceCloneBankSaveRequest, VoiceCloneCandidates, VoiceCloneExtractRequest,
                         VoiceCloneJobStarted, VoiceCloneRemoveRequest, VoiceCloneSeriesLinkRequest)
from services import voice_clone_service

router = APIRouter(prefix="/api/characters", tags=["voice-clone"])

_ERR = {404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}}
_CANDIDATE_ID = Path(min_length=32, max_length=32, pattern="^[0-9a-f]{32}$")


@router.post("/dramas/{drama_id}/reference-clip", dependencies=[local_only()],
             response_model=CharactersEntry,
             summary="Upload (or replace) one speaker's clone reference clip",
             responses={**_ERR, 409: {"model": ErrorResponse}, 503: {"model": ErrorResponse}})
def post_reference_clip(drama_id: int = Path(ge=1), file: UploadFile = File(...),
                        speaker_label: str = Form(..., min_length=1, max_length=200),
                        ref_text: Optional[str] = Form(None, max_length=5000)):
    return voice_clone_service.upload_reference_clip(
        drama_id, speaker_label, file.filename, file.file, ref_text=ref_text)


@router.post("/dramas/{drama_id}/reference-clip/remove", dependencies=[local_only()],
             response_model=CharactersEntry,
             summary="Remove one speaker's clone reference clip (confirm=true)",
             responses={**_ERR, 409: {"model": ErrorResponse}})
def post_remove_reference_clip(body: VoiceCloneRemoveRequest, drama_id: int = Path(ge=1)):
    return voice_clone_service.remove_reference_clip(drama_id, body.speaker_label,
                                                     confirm=body.confirm)


@router.post("/dramas/{drama_id}/reference-clips/extract",
             dependencies=[require_permission("jobs.start")], response_model=VoiceCloneJobStarted,
             summary="Start extracting candidate reference clips for one speaker "
                     "(poll GET /api/jobs/{job_id})",
             responses={**_ERR, 409: {"model": ErrorResponse}, 503: {"model": ErrorResponse}})
def post_extract_candidates(body: VoiceCloneExtractRequest, drama_id: int = Path(ge=1)):
    return voice_clone_service.start_extract_candidates(
        drama_id, body.speaker_label, max_candidates=body.max_candidates)


@router.get("/dramas/{drama_id}/reference-clips/candidates",
            dependencies=[require_permission("lines.read")], response_model=VoiceCloneCandidates,
            summary="Candidate reference clips from each speaker's last extraction",
            responses={404: {"model": ErrorResponse}})
def get_candidates(drama_id: int = Path(ge=1)):
    return voice_clone_service.list_candidates(drama_id)


@router.get("/dramas/{drama_id}/reference-clips/candidates/{candidate_id}/audio",
            dependencies=[require_permission("media.stream")],
            summary="Play one candidate clip (WAV)",
            responses={404: {"model": ErrorResponse}})
def get_candidate_audio(drama_id: int = Path(ge=1), candidate_id: str = _CANDIDATE_ID):
    path = voice_clone_service.candidate_audio_path(drama_id, candidate_id)
    return FileResponse(path, media_type="audio/wav", filename=f"candidate_{drama_id}.wav",
                        content_disposition_type="inline",
                        headers={"Cache-Control": "no-store",
                                 "X-Content-Type-Options": "nosniff"})


@router.post("/dramas/{drama_id}/reference-clips/candidates/{candidate_id}/choose",
             dependencies=[require_permission("lines.edit")], response_model=CharactersEntry,
             summary="Use one candidate as its speaker's clone reference",
             responses=_ERR)
def post_choose_candidate(drama_id: int = Path(ge=1), candidate_id: str = _CANDIDATE_ID):
    return voice_clone_service.choose_candidate(drama_id, candidate_id)


@router.post("/dramas/{drama_id}/voice-bank/save",
             dependencies=[require_permission("admin.library")],
             response_model=CharactersVoiceBankEntry,
             summary="Save one speaker's clip and voice settings to the voice bank",
             responses=_ERR)
def post_save_to_voice_bank(body: VoiceCloneBankSaveRequest, drama_id: int = Path(ge=1)):
    return voice_clone_service.save_to_voice_bank(drama_id, body.speaker_label, body.name,
                                                  notes=body.notes)


@router.post("/dramas/{drama_id}/series-link", dependencies=[require_permission("lines.edit")],
             response_model=CharactersEntry,
             summary="Link a speaker to a known series character (null unlinks)",
             responses=_ERR)
def post_series_link(body: VoiceCloneSeriesLinkRequest, drama_id: int = Path(ge=1)):
    return voice_clone_service.link_series_character(drama_id, body.speaker_label,
                                                     body.series_character_id)
