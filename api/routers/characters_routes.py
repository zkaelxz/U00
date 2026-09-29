"""
api/routers/characters_routes.py -- per-drama characters and voice config
(Phase 6, Migration Slice 42).

Speaker labels can contain spaces, unicode or slashes, so they travel in
JSON bodies, never path segments. Only fields the client actually sets
are forwarded to the service (omitted = leave alone, "" = clear).

Also here: recurring-voice suggestions (C02: list, accept, reject) and
"remember as a known series character" (C08).

Out of scope: reference-audio upload / auto-extract (needs multipart),
series-character rename/delete, and Dub generation itself.
"""

from typing import List

from fastapi import APIRouter, Path
from api.auth import require_permission
from api.schemas import (CharactersCloneEngines, CharactersEntry, CharactersRememberRequest,
                         CharactersRememberResult, CharactersSeriesEntry,
                         CharactersUpdateRequest, CharactersVoiceBankApply,
                         CharactersVoiceBankEntry, CharactersVoiceSuggestion,
                         CharactersVoiceSuggestionRequest, CharactersVoiceSuggestionResult,
                         ErrorResponse)
from services import characters_service

router = APIRouter(prefix="/api/characters", tags=["characters"])


@router.get("/dramas/{drama_id}", dependencies=[require_permission("library.read")], response_model=List[CharactersEntry],
            summary="Speakers of one drama with their character/voice settings",
            responses={404: {"model": ErrorResponse}})
def get_characters(drama_id: int = Path(ge=1)):
    return characters_service.list_characters(drama_id)


@router.post("/dramas/{drama_id}/character", dependencies=[require_permission("lines.edit")], response_model=CharactersEntry,
             summary="Partially update one speaker's character/voice settings",
             responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}})
def post_character(payload: CharactersUpdateRequest, drama_id: int = Path(ge=1)):
    fields = payload.model_dump(exclude_unset=True)
    speaker_label = fields.pop("speaker_label")
    return characters_service.update_character(drama_id, speaker_label, **fields)


@router.get("/dramas/{drama_id}/clone-engines", dependencies=[require_permission("library.read")], response_model=CharactersCloneEngines,
            summary="Clone engines usable for this drama's source language",
            responses={404: {"model": ErrorResponse}})
def get_clone_engines(drama_id: int = Path(ge=1)):
    return characters_service.get_clone_engine_options(drama_id)


@router.get("/series/{series_id}/characters", dependencies=[require_permission("library.read")], response_model=List[CharactersSeriesEntry],
            summary="A series' characters")
def get_series_characters(series_id: int = Path(ge=1)):
    return characters_service.list_series_characters(series_id)


@router.get("/voice-bank", dependencies=[require_permission("library.read")], response_model=List[CharactersVoiceBankEntry],
            summary="Voice bank picklist (metadata only)")
def get_voice_bank():
    return characters_service.list_voice_bank()


@router.post("/dramas/{drama_id}/voice-bank/apply", dependencies=[require_permission("lines.edit")], response_model=CharactersEntry,
             summary="Apply a voice bank entry to one speaker of this drama",
             responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}})
def post_voice_bank_apply(payload: CharactersVoiceBankApply, drama_id: int = Path(ge=1)):
    return characters_service.apply_voice_bank_entry(
        drama_id, payload.speaker_label, payload.voice_bank_id)


@router.get("/dramas/{drama_id}/voice-suggestions", dependencies=[require_permission("library.read")],
            response_model=List[CharactersVoiceSuggestion],
            summary="Experimental 'sounds like' matches against the series cast (empty when unavailable)",
            responses={404: {"model": ErrorResponse}})
def get_voice_suggestions(drama_id: int = Path(ge=1)):
    return characters_service.list_voice_suggestions(drama_id)


@router.post("/dramas/{drama_id}/voice-suggestions/accept", dependencies=[require_permission("lines.edit")],
             response_model=CharactersVoiceSuggestionResult,
             summary="Accept an offered voice suggestion: name and link the speaker",
             responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}})
def post_voice_suggestion_accept(payload: CharactersVoiceSuggestionRequest, drama_id: int = Path(ge=1)):
    return characters_service.accept_voice_suggestion(
        drama_id, payload.speaker_label, payload.series_character_id)


@router.post("/dramas/{drama_id}/voice-suggestions/reject", dependencies=[require_permission("lines.edit")],
             response_model=CharactersVoiceSuggestionResult,
             summary="Dismiss a voice suggestion for this drama",
             responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}})
def post_voice_suggestion_reject(payload: CharactersVoiceSuggestionRequest, drama_id: int = Path(ge=1)):
    return characters_service.reject_voice_suggestion(
        drama_id, payload.speaker_label, payload.series_character_id)


@router.post("/dramas/{drama_id}/remember-series-character", dependencies=[require_permission("lines.edit")],
             response_model=CharactersRememberResult,
             summary="Add a speaker's saved name to the drama's series cast and link it",
             responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
                        422: {"model": ErrorResponse}})
def post_remember_series_character(payload: CharactersRememberRequest, drama_id: int = Path(ge=1)):
    return characters_service.remember_series_character(drama_id, payload.speaker_label)
