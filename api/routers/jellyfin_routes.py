"""
api/routers/jellyfin_routes.py -- the optional Jellyfin connector. See services/jellyfin_service.py.

Every route is local_only(); the key routes, and a change of server address
(which moves where the key is sent), also take the engine-key gate
(`_require_local_admin`, key writes allowed in the API settings) and
confirm=true. The connector's settings hold a key and a
server address, the scan reads another server's library, and a send writes
files outside the drama folder, so all of it stays at the PC.
"""

from typing import Optional

from fastapi import APIRouter, Path, Request

from api.auth import local_only
from api.jellyfin_schemas import (JellyfinConfig, JellyfinConfigUpdate, JellyfinKeyClear,
                                  JellyfinKeySet, JellyfinScanReport, JellyfinScanRequest,
                                  JellyfinSendRequest, JellyfinSendResult, JellyfinTestResult)
from api.routers.settings_routes import read_body, require_confirm, require_local_admin
from api.schemas import error_responses
from services import jellyfin_service

router = APIRouter(prefix="/api/jellyfin", tags=["jellyfin"])

_ERRS = error_responses(409, 422, 503)


@router.get("/config", dependencies=[local_only()], response_model=JellyfinConfig,
            summary="PC only: the Jellyfin connector settings (the key as a boolean)")
def get_config():
    return jellyfin_service.get_config()


@router.post("/config", dependencies=[local_only()], response_model=JellyfinConfig,
             summary="PC only: save the Jellyfin library folder or on/off; the address also "
                     "needs the key-write gate and confirm",
             responses=error_responses(422))
def set_config(payload: JellyfinConfigUpdate, request: Request):
    if payload.server_url is not None:  # where the key goes: same gate as endpoint URLs
        require_local_admin(request)
        require_confirm(payload.confirm)
    return jellyfin_service.set_config(enabled=payload.enabled, server_url=payload.server_url,
                                       library_dir=payload.library_dir)


@router.post("/key", dependencies=[local_only()], response_model=JellyfinConfig,
             summary="PC only: set the Jellyfin API key (write-only; same gate as engine keys)",
             responses=error_responses(422))
async def set_key(request: Request):
    require_local_admin(request)
    body = await read_body(request, JellyfinKeySet)
    require_confirm(body.confirm)
    return jellyfin_service.set_key(body.value)


@router.post("/key/clear", dependencies=[local_only()], response_model=JellyfinConfig,
             summary="PC only: remove the Jellyfin API key from .env",
             responses=error_responses(422))
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


def _ass_options(payload: JellyfinSendRequest) -> Optional[dict]:
    if payload.ass is None:
        return None
    options = payload.ass.model_dump(exclude={"field", "style"})
    if payload.ass.style is not None:
        options["style"] = payload.ass.style.model_dump(exclude_unset=True)
    return options


@router.post("/dramas/{drama_id}/send", dependencies=[local_only()],
             response_model=JellyfinSendResult,
             summary="PC only: put the drama's subtitles (and optionally video) into the "
                     "Jellyfin library, then refresh",
             responses=error_responses(409, 422, 503, 404))
def send(payload: JellyfinSendRequest, drama_id: int = Path(ge=1)):
    return jellyfin_service.send_to_jellyfin(
        drama_id, item_id=payload.item_id, fmt=payload.format, field=payload.field,
        language=payload.language, media=payload.media, overwrite=payload.overwrite,
        refresh=payload.refresh, ass=_ass_options(payload))
