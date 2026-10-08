"""
api/routers/delete_routes.py -- the PC-only deletes that had no API yet.
Thin adapters over
`services.delete_service`.

Every route is a POST, declared `local_only()` (deletes are PC-only, see
docs/remote-access-decision.md), and needs `{"confirm": true}` in the body
(422 otherwise). No typed confirmation word. The paths sit
under the prefix of the resource they delete; they live in their own router
so the owning routers stay unchanged.
"""

from fastapi import APIRouter, Path

from api.auth import local_only
from api.schemas import (DeleteConfirm, ErrorResponse,
                         MediaRemoveResult, PresetDeleteResult, RawNovelRemoveResult, ReadingHistoryClearResult,
                         SeriesCharacterDeleteResult, TranslationVersionDeleteResult,
                         VoiceBankDeleteResult)
from services import delete_service as svc

router = APIRouter(prefix="/api", tags=["deletes"])

_ERRS = {404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
         422: {"model": ErrorResponse}}


def _id():
    return Path(ge=1, le=svc.MAX_ID)


@router.post("/media/dramas/{drama_id}/remove", dependencies=[local_only()],
             response_model=MediaRemoveResult, responses=_ERRS,
             summary="Remove a drama's audio/video files (confirm=true; 409 while a job runs)")
def remove_media(body: DeleteConfirm, drama_id: int = _id()):
    return svc.remove_media(drama_id, confirm=body.confirm)


@router.post("/novel/dramas/{drama_id}/raw-novel/remove", dependencies=[local_only()],
             response_model=RawNovelRemoveResult, responses=_ERRS,
             summary="Remove a drama's raw novel text (confirm=true; 409 while a job runs)")
def remove_raw_novel(body: DeleteConfirm, drama_id: int = _id()):
    return svc.remove_raw_novel(drama_id, confirm=body.confirm)


@router.post("/review/dramas/{drama_id}/versions/{version_id}/delete", dependencies=[local_only()],
             response_model=TranslationVersionDeleteResult, responses=_ERRS,
             summary="Delete a saved translation version (confirm=true; 409 while a job runs)")
def delete_version(body: DeleteConfirm, drama_id: int = _id(), version_id: int = _id()):
    return svc.delete_translation_version(drama_id, version_id, confirm=body.confirm)


@router.post("/characters/series/{series_id}/characters/{character_id}/delete",
             dependencies=[local_only()], response_model=SeriesCharacterDeleteResult,
             responses=_ERRS, summary="Delete a series character (confirm=true)")
def delete_series_character(body: DeleteConfirm, series_id: int = _id(), character_id: int = _id()):
    return svc.delete_series_character(series_id, character_id, confirm=body.confirm)


@router.post("/library/presets/{preset_id}/delete", dependencies=[local_only()],
             response_model=PresetDeleteResult, responses=_ERRS,
             summary="Delete a preset (confirm=true)")
def delete_preset(body: DeleteConfirm, preset_id: int = _id()):
    return svc.delete_preset(preset_id, confirm=body.confirm)


@router.post("/library/voice-bank/{entry_id}/delete", dependencies=[local_only()],
             response_model=VoiceBankDeleteResult, responses=_ERRS,
             summary="Delete a voice bank entry and its clip (confirm=true)")
def delete_voice_bank_entry(body: DeleteConfirm, entry_id: int = _id()):
    return svc.delete_voice_bank_entry(entry_id, confirm=body.confirm)


@router.post("/library/history/clear", dependencies=[local_only()],
             response_model=ReadingHistoryClearResult, responses={422: {"model": ErrorResponse}},
             summary="Clear the reading history (confirm=true; reading progress is kept)")
def clear_reading_history(body: DeleteConfirm):
    return svc.clear_reading_history(confirm=body.confirm)
