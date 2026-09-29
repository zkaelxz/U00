"""
api/routers/series_people_routes.py -- add and edit a series' people
(parity audit B1 #8, X15-X17). Thin adapters over
`services.series_people_service`.

Both routes are `lines.edit`, the same permission as the per-drama
character edit (`POST /api/characters/dramas/{drama_id}/character`).
Listing stays in characters_routes; deleting stays PC-only in
delete_routes. A person is addressed by id within its series, never by
name or position.
"""

from fastapi import APIRouter, Path

from api.auth import require_permission
from api.schemas import (CharactersSeriesEntry, ErrorResponse, SeriesPersonCreate,
                         SeriesPersonUpdate)
from services import series_people_service as svc

router = APIRouter(prefix="/api/characters/series", tags=["characters"])

_ERRS = {404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
         422: {"model": ErrorResponse}}


def _id():
    return Path(ge=1, le=svc.MAX_ID)


@router.post("/{series_id}/characters", dependencies=[require_permission("lines.edit")],
             response_model=CharactersSeriesEntry, responses=_ERRS,
             summary="Add a person to a series (409 if the name is taken)")
def add_series_person(payload: SeriesPersonCreate, series_id: int = _id()):
    return svc.add_person(series_id, payload.character_name, pronouns=payload.pronouns,
                          aliases=payload.aliases, notes=payload.notes)


@router.post("/{series_id}/characters/{character_id}", dependencies=[require_permission("lines.edit")],
             response_model=CharactersSeriesEntry, responses=_ERRS,
             summary="Rename a series person or set their pronouns, aliases or notes")
def update_series_person(payload: SeriesPersonUpdate, series_id: int = _id(),
                         character_id: int = _id()):
    fields = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    return svc.update_person(series_id, character_id, **fields)
