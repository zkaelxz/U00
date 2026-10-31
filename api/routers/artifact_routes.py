"""
api/routers/artifact_routes.py -- download a job's output file. Addressed by drama + whitelisted kind; the newest file in that
kind's folder is streamed. No client path is ever accepted.
"""

import os

from fastapi import APIRouter, Path, Request
from api.auth import require_permission
from fastapi.responses import FileResponse

from api.schemas import ArtifactInfo, error_responses
from lib.safe_paths import download_name
from services import artifact_service, export_service
from services.service_errors import ForbiddenError
from lib.storage_errors import require_readable

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])

_ERRORS = error_responses(404, 422)
# Line text, which every other route gates on lines.read (a soft-sub video
# carries the subtitle track as text); the media kinds (audio, burned-in
# video, typeset comic pages) are media.stream alone.
_TEXT_KINDS = frozenset(("subtitle", "epub", "softsub_video"))


@router.get("/dramas/{drama_id}/{kind}/info", dependencies=[require_permission("library.read")], response_model=ArtifactInfo,
            summary="Name, size and kind of the newest artifact (no path)", responses=_ERRORS)
def get_artifact_info(drama_id: int = Path(ge=1), kind: str = Path(max_length=40)):
    art = artifact_service.get_artifact(drama_id, kind)
    return {"name": art["name"], "size": art["size"], "kind": art["kind"]}


@router.get("/dramas/{drama_id}/{kind}", dependencies=[require_permission("media.stream")],
            summary="Download the newest artifact of one kind for a drama",
            responses=error_responses(403, 404, 422))
def download_artifact(request: Request, drama_id: int = Path(ge=1),
                      kind: str = Path(max_length=40)):
    if kind in _TEXT_KINDS and "lines.read" not in request.state.principal["permissions"]:
        raise ForbiddenError("Not allowed.")
    art = artifact_service.get_artifact(drama_id, kind)
    require_readable(art["path"])
    what = export_service.ARTIFACT_WHAT.get(kind)
    if what:
        header = export_service.content_disposition(export_service.download_filename(
            drama_id, what, art["language"], os.path.splitext(art["name"])[1]))
    else:
        header = f'attachment; filename="{download_name(art["name"])}"'
    return FileResponse(art["path"], media_type="application/octet-stream",
                        headers={"Content-Disposition": header})
