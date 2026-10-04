"""
api/routers/artifact_routes.py -- download a job's output file. Addressed by drama + whitelisted kind; the newest file in that
kind's folder is streamed. No client path is ever accepted.
"""

import re

from fastapi import APIRouter, Path
from api.auth import require_permission
from fastapi.responses import FileResponse

from api.schemas import ArtifactInfo, ErrorResponse
from services import artifact_service

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])

_ERRORS = {404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}}


def _safe_name(name: str) -> str:
    return re.sub(r'[^A-Za-z0-9._-]', "_", name) or "download"


@router.get("/dramas/{drama_id}/{kind}/info", dependencies=[require_permission("library.read")], response_model=ArtifactInfo,
            summary="Name, size and kind of the newest artifact (no path)", responses=_ERRORS)
def get_artifact_info(drama_id: int = Path(ge=1), kind: str = Path(max_length=40)):
    art = artifact_service.get_artifact(drama_id, kind)
    return {"name": art["name"], "size": art["size"], "kind": art["kind"]}


@router.get("/dramas/{drama_id}/{kind}", dependencies=[require_permission("media.stream")],
            summary="Download the newest artifact of one kind for a drama", responses=_ERRORS)
def download_artifact(drama_id: int = Path(ge=1), kind: str = Path(max_length=40)):
    art = artifact_service.get_artifact(drama_id, kind)
    return FileResponse(
        art["path"], media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{_safe_name(art["name"])}"'})
