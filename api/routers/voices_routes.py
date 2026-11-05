"""api/routers/voices_routes.py -- the Voices screen: list enrolled voice prints, and clear, retrain or trim one. Thin adapters over services.voices_service; changes are PC only like every other delete."""

from fastapi import APIRouter, Path, Request

from api.auth import local_only, require_permission
from api.schemas import (VoiceChangeResult, VoiceClearRequest, VoiceListResponse,
                         VoiceRemoveSampleRequest, VoiceRetrainRequest, error_responses)
from lib.ids import MAX_ID
from services import voices_service as svc

router = APIRouter(prefix="/api/voices", tags=["characters"])

_ERRS = error_responses(404, 409, 422)


def _id():
    return Path(ge=1, le=MAX_ID)


@router.get("", dependencies=[require_permission("library.read")], response_model=VoiceListResponse,
            summary="Every enrolled voice print, with its samples and match scores")
def list_voices(request: Request):
    return {"items": svc.list_voices(principal=request.state.principal)}


@router.post("/{series_id}/{character_id}/clear", dependencies=[local_only()],
             response_model=VoiceChangeResult, responses=_ERRS,
             summary="Clear a person's voice print, keeping the person (confirm=true)")
def clear_voice(body: VoiceClearRequest, series_id: int = _id(), character_id: int = _id()):
    return svc.clear_voice(series_id, character_id, confirm=body.confirm)


@router.post("/{series_id}/{character_id}/retrain", dependencies=[local_only()],
             response_model=VoiceChangeResult, responses=_ERRS,
             summary="Rebuild a voice print from the speakers linked to the person (confirm=true)")
def retrain_voice(body: VoiceRetrainRequest, series_id: int = _id(), character_id: int = _id()):
    return svc.retrain_voice(series_id, character_id, confirm=body.confirm)


@router.post("/{series_id}/{character_id}/remove-sample", dependencies=[local_only()],
             response_model=VoiceChangeResult, responses=_ERRS,
             summary="Unlink one speaker and rebuild the print without it (confirm=true)")
def remove_sample(body: VoiceRemoveSampleRequest, series_id: int = _id(), character_id: int = _id()):
    return svc.remove_sample(series_id, character_id, body.drama_id, body.speaker_label,
                             confirm=body.confirm)
