"""
api/routers/jellyfin_routes.py -- the optional Jellyfin connector. See services/jellyfin_service.py.

Every route is local_only(); the key routes, and a change of server address
(which moves where the key is sent), also take the engine-key gate
(`_require_local_admin`, key writes allowed in the API settings) and
confirm=true. The connector's settings hold a key and a
server address, the scan reads another server's library, and a send writes
files outside the drama folder, so all of it stays at the PC.
"""

from fastapi import APIRouter, Path, Request

from api.auth import local_only
from api.jellyfin_schemas import (JellyfinConfig, JellyfinConfigUpdate, JellyfinKeyClear,
                                  JellyfinKeySet, JellyfinScanReport, JellyfinScanRequest,
                                  JellyfinSendRequest, JellyfinSendResult, JellyfinTestResult)
from api.routers.settings_routes import read_body, require_confirm, require_local_admin
from api.schemas import ErrorResponse
from services import jellyfin_service

router = APIRouter(prefix="/api/jellyfin", tags=["jellyfin"])

_ERRS = {409: {"model": ErrorResponse}, 422: {"model": ErrorResponse},
         503: {"model": ErrorResponse}}


@router.get("/config", dependencies=[local_only()], response_model=JellyfinConfig,
            summary="PC only: the Jellyfin connector settings (the key as a boolean)")
def get_config():
    return jellyfin_service.get_config()


@router.post("/config", dependencies=[local_only()], response_model=JellyfinConfig,
             summary="PC only: save the Jellyfin library folder or on/off; the address also "
                     "needs the key-write gate and confirm",
             responses={422: {"model": ErrorResponse}})
def set_config(payload: JellyfinConfigUpdate, request: Request):
    if payload.server_url is not None:  # where the key goes: same gate as endpoint URLs
        require_local_admin(request)
        require_confirm(payload.confirm)
    return jellyfin_service.set_config(enabled=payload.enabled, server_url=payload.server_url,
                                       library_dir=payload.library_dir)


@router.post("/key", dependencies=[local_only()], response_model=JellyfinConfig,
             summary="PC only: set the Jellyfin API key (write-only; same gate as engine keys)",
             responses={422: {"model": ErrorResponse}})
async def set_key(request: Request):
    require_local_admin(request)
    body = await read_body(request, JellyfinKeySet)
    require_confirm(body.confirm)
    return jellyfin_service.set_key(body.value)


@router.post("/key/clear", dependencies=[local_only()], response_model=JellyfinConfig,
             summary="PC only: remove the Jellyfin API key from .env",
             responses={422: {"model": ErrorResponse}})
async def clear_key(request: Request):
    require_local_admin(request)
    body = await read_body(request, JellyfinKeyClear)
    require_confirm(body.confirm)
    return jellyfin_service.clear_key()


@router.post("/test", dependencies=[local_only()], response_model=JellyfinTestResult,
             summary="PC only: check the Jellyfin address and key", responses=_ERRS)
def test_connection():
    return jellyfin_service.test_connection()


@router.post("/scan", dependencies=[local_only()], response_model=JellyfinScanReport,
             summary="PC only: report items missing a subtitle language (changes nothing)",
             responses=_ERRS)
def scan(payload: JellyfinScanRequest):
    return jellyfin_service.scan(payload.language)


@router.post("/dramas/{drama_id}/send", dependencies=[local_only()],
             response_model=JellyfinSendResult,
             summary="PC only: put the drama's subtitles (and optionally video) into the "
                     "Jellyfin library, then refresh",
             responses={**_ERRS, 404: {"model": ErrorResponse}})
def send(payload: JellyfinSendRequest, drama_id: int = Path(ge=1)):
    return jellyfin_service.send_to_jellyfin(
        drama_id, item_id=payload.item_id, fmt=payload.format, field=payload.field,
        language=payload.language, media=payload.media, overwrite=payload.overwrite,
        refresh=payload.refresh)
