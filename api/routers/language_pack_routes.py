"""
api/routers/language_pack_routes.py -- built-in language packs. Thin: see
services/language_pack_service.py.

- `GET /api/language-packs` and `GET .../packs/{pack_id}` (`library.read`):
  the packs, and one pack's entries.
- `GET|POST .../dramas/{drama_id}` (`library.read` / `lines.edit`): which packs
  a title uses.
- `POST .../defaults/{language}` (`admin.settings`): the choice titles of that
  source language follow until they get their own.
"""

from typing import List

from fastapi import APIRouter, Path, Request

from api.auth import require_permission
from api.schemas import (ErrorResponse, LanguagePack, LanguagePackChoice,
                         LanguagePackDefaultResult, LanguagePackSummary, TitleLanguagePacks)
from services import language_pack_service

router = APIRouter(prefix="/api/language-packs", tags=["glossary"])


@router.get("", dependencies=[require_permission("library.read")], response_model=List[LanguagePackSummary],
            summary="The built-in language packs (no entries)")
def list_packs():
    return language_pack_service.list_packs()


@router.get("/packs/{pack_id}", dependencies=[require_permission("library.read")], response_model=LanguagePack,
            summary="One language pack with its entries",
            responses={404: {"model": ErrorResponse}})
def get_pack(pack_id: str = Path(max_length=40)):
    return language_pack_service.get_pack(pack_id)


@router.get("/dramas/{drama_id}", dependencies=[require_permission("library.read")],
            response_model=TitleLanguagePacks,
            summary="The packs that fit the title's source language, and which are on",
            responses={404: {"model": ErrorResponse}})
def get_title_packs(request: Request, drama_id: int = Path(ge=1)):
    return language_pack_service.get_title_packs(drama_id, principal=request.state.principal)


@router.post("/dramas/{drama_id}", dependencies=[require_permission("lines.edit")],
             response_model=TitleLanguagePacks,
             summary="Choose the packs (and their styles) for this title",
             responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
                        422: {"model": ErrorResponse}})
def set_title_packs(request: Request, payload: LanguagePackChoice, drama_id: int = Path(ge=1)):
    return language_pack_service.set_title_packs(drama_id, payload.model_dump(), principal=request.state.principal)


@router.post("/defaults/{language}", dependencies=[require_permission("admin.settings")],
             response_model=LanguagePackDefaultResult,
             summary="Set the packs used by titles of a source language that have no choice of their own",
             responses={422: {"model": ErrorResponse}})
def set_language_default(payload: LanguagePackChoice, language: str = Path(max_length=2)):
    return language_pack_service.set_language_default(language, payload.model_dump())
