"""
api/routers/voice_bank_audio_routes.py -- play one voice-bank entry's
saved clip (L19). `media.stream` (opt-in, like every other media byte
route); `entry_id` is household-wide (voice bank, decision 6). Only an
audio file inside the voice-bank folder is served (see
services/voice_bank_audio_service.py), under a generic file name, with
nosniff; Range and HEAD come from FileResponse.
"""

from fastapi import APIRouter, Path
from fastapi.responses import FileResponse

from api.auth import require_permission
from api.schemas import ErrorResponse
from services import voice_bank_audio_service

router = APIRouter(prefix="/api/library", tags=["library"])

_STREAM = [require_permission("media.stream")]


@router.head("/voice-bank/{entry_id}/audio", dependencies=_STREAM, include_in_schema=False)
@router.get("/voice-bank/{entry_id}/audio", dependencies=_STREAM,
            summary="Play a voice-bank entry's clip (audio only, Range supported)",
            responses={200: {"content": {"audio/*": {}}}, 206: {"content": {"audio/*": {}}},
                       404: {"model": ErrorResponse},
                       416: {"description": "Range not satisfiable"}})
def get_voice_bank_audio(entry_id: int = Path(ge=1)):
    path, ctype, ext = voice_bank_audio_service.clip_for_entry(entry_id)
    return FileResponse(path, media_type=ctype, filename=f"voice_{entry_id}{ext}",
                        content_disposition_type="inline",
                        headers={"Cache-Control": "no-store",
                                 "X-Content-Type-Options": "nosniff"})
