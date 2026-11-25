"""
api/routers/cast_routes.py -- cast a title's English-subtitled copy to a TV.

    GET  /api/cast/dramas/{drama_id}         media.stream  is the subtitled copy ready for a TV
    POST /api/cast/dramas/{drama_id}/link    media.stream  a link to it for the next 6 hours
    GET  /api/cast/video?k={secret}          public_route  the copy itself, for the TV (Range)

A TV has no sign-in, so the last route is the one place a long random secret in the URL stands for a session.
It is a query value because the early auth gate admits only fixed public paths, and it must stay one:
Caddy's logs drop the query but would write a path segment. A link stops working when its account is
deactivated, loses media.stream or can no longer see the title, and counts against that account's open
streams like any other stream. It serves one file, picked by the server from the secret's title, never a client path.
Over the household listener the access log is off, so the secret is not written down; the admin
listener's (loopback) log is not, and the page never builds a loopback link.
"""

import os

from fastapi import APIRouter, Path, Query, Request

from api.auth import public_route, require_permission
from api.routers.media_routes import _CappedFileResponse
from api.schemas import CastLink, CastStatus, error_responses
from lib.storage_errors import require_readable
from services import cast_service

router = APIRouter(prefix="/api/cast", tags=["cast"])

_DRAMA = Path(ge=1, lt=2**63)


@router.get("/dramas/{drama_id}", dependencies=[require_permission("media.stream")], response_model=CastStatus,
            summary="Whether the title's subtitled copy is ready to cast", responses=error_responses(404))
def get_cast_status(drama_id: int = _DRAMA):
    return cast_service.status(drama_id)


@router.post("/dramas/{drama_id}/link", dependencies=[require_permission("media.stream")], response_model=CastLink,
             summary="A link a TV can play the subtitled copy from", responses=error_responses(404, 409))
def post_cast_link(request: Request, drama_id: int = _DRAMA):
    return cast_service.create_link(request.state.principal, drama_id)


@router.head("/video", dependencies=[public_route()], include_in_schema=False)
@router.get("/video", dependencies=[public_route()],
            summary="The subtitled copy for a TV, by its cast link (no sign-in)",
            responses={200: {"content": {"video/*": {}}}, 206: {"content": {"video/*": {}}}, **error_responses(404)})
def get_cast_video(k: str = Query(min_length=20, max_length=80)):
    path, ctype, uid = cast_service.resolve(k)
    require_readable(path)
    # The receiver's player fetches from another origin; the secret, not the origin, is the credential.
    return _CappedFileResponse(path, media_type=ctype, user_id=uid, filename=f"episode{os.path.splitext(path)[1]}",
                        content_disposition_type="inline",
                        headers={"X-Content-Type-Options": "nosniff", "Access-Control-Allow-Origin": "*",
                                 "Cache-Control": "private, no-store"})
