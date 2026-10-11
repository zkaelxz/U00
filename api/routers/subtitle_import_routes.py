"""
api/routers/subtitle_import_routes.py -- import a timed subtitle or lyric file
(SRT, VTT, ASS/SSA, LRC) into one drama, and rank subtitle file names against a
media file name. The logic is in services/subtitle_import_service.py.

Both file routes take multipart/form-data: a 'file' part plus small text
fields. The upload is refused past the file cap before it is read; the file
itself is parsed in memory and never stored.

Preview and apply are local_only(): uploads are PC-only unless the owner
records a decision otherwise (docs/remote-access-decision.md). The sidecar
ranking takes names only, so it stays on lines.edit.
"""

from fastapi import APIRouter, Path, Request
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException

import subtitle_parse
from api.auth import local_only, require_permission
from api.routers.bug_report_routes import BodyTooLarge, capped
from api.schemas import (ErrorResponse, SidecarMatchRequest, SidecarMatchResult,
                         SubtitleImportPreview, SubtitleImportResult)
from services import subtitle_import_service as svc
from services.service_errors import InvalidInputError

router = APIRouter(prefix="/api/subtitle-import", tags=["subtitle-import"])

_MULTIPART_OVERHEAD = 64 * 1024
_FORM_HELP = ("Send the subtitle file as multipart/form-data in a 'file' field; options "
              "(mode, split_bilingual, translation_first, encoding) are text fields.")
_FORM_BODY = {"requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
    "type": "object", "required": ["file"],
    "properties": {"file": {"type": "string", "format": "binary"},
                   "mode": {"type": "string", "enum": list(svc.MODES)},
                   "encoding": {"type": "string"},
                   "split_bilingual": {"type": "string"},
                   "translation_first": {"type": "string"},
                   "confirm_replace_lines": {"type": "string"},
                   "confirm_overwrite": {"type": "string"}}}}}}}
_ERRORS = {404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
           413: {"model": ErrorResponse}, 422: {"model": ErrorResponse}}


async def _read_form(request: Request):
    if "transfer-encoding" in request.headers:
        raise InvalidInputError("Send the file with a Content-Length, not chunked.")
    try:
        length = int(request.headers.get("content-length", ""))
    except ValueError:
        raise InvalidInputError("An upload needs a Content-Length.") from None
    cap = subtitle_parse.MAX_FILE_BYTES + _MULTIPART_OVERHEAD
    too_large = f"That file is too large (at most {subtitle_parse.MAX_FILE_BYTES // (1024 * 1024)} MB)."
    if length > cap:
        raise StarletteHTTPException(413, too_large)
    try:
        return await capped(request, cap).form(max_files=1, max_fields=8, max_part_size=1024)
    except BodyTooLarge:
        raise StarletteHTTPException(413, too_large) from None
    except (MultiPartException, StarletteHTTPException):
        raise InvalidInputError(_FORM_HELP) from None


def _flag(form, name: str) -> bool:
    return form.get(name) == "true"


def _text(form, name: str, default: str = "") -> str:
    value = form.get(name, default)
    return value if isinstance(value, str) else default


async def _file_and_options(request: Request):
    form = await _read_form(request)
    try:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise InvalidInputError(_FORM_HELP)
        # One byte over the cap is enough for the parser to refuse it by size.
        data = await upload.read(subtitle_parse.MAX_FILE_BYTES + 1)
        return data, upload.filename or "", {
            "encoding": _text(form, "encoding") or None,
            "mode": _text(form, "mode", "source"),
            "split_bilingual": _flag(form, "split_bilingual"),
            "translation_first": _flag(form, "translation_first"),
            "confirm_replace_lines": _flag(form, "confirm_replace_lines"),
            "confirm_overwrite": _flag(form, "confirm_overwrite"),
        }
    finally:
        await form.close()


@router.post("/dramas/{drama_id}/preview", dependencies=[local_only()],
             response_model=SubtitleImportPreview, openapi_extra=_FORM_BODY, responses=_ERRORS,
             summary="Parse and check an uploaded subtitle file; says what importing it would do, changes nothing")
async def post_preview(request: Request, drama_id: int = Path(ge=1)):
    data, filename, opts = await _file_and_options(request)
    return await run_in_threadpool(
        svc.preview_import, drama_id, data, filename, encoding=opts["encoding"], mode=opts["mode"],
        split_bilingual=opts["split_bilingual"], translation_first=opts["translation_first"])


@router.post("/dramas/{drama_id}/apply", dependencies=[local_only()],
             response_model=SubtitleImportResult, openapi_extra=_FORM_BODY, responses=_ERRORS,
             summary="Import an uploaded subtitle file as source lines or as translation text",
             description="Replacing existing lines needs confirm_replace_lines=true and overwriting "
                         "existing translations needs confirm_overwrite=true (422 with "
                         "details.reason otherwise). The old lines are saved to history first; "
                         "the result carries an undo handle for POST /api/restructure/.../history/.../restore.")
async def post_apply(request: Request, drama_id: int = Path(ge=1)):
    data, filename, opts = await _file_and_options(request)
    return await run_in_threadpool(
        svc.import_subtitle, drama_id, data, filename, encoding=opts["encoding"], mode=opts["mode"],
        split_bilingual=opts["split_bilingual"], translation_first=opts["translation_first"],
        confirm_replace_lines=opts["confirm_replace_lines"], confirm_overwrite=opts["confirm_overwrite"])


@router.post("/dramas/{drama_id}/sidecars", dependencies=[require_permission("lines.edit")],
             response_model=SidecarMatchResult, responses=_ERRORS,
             summary="Rank subtitle file names against a media file name (names only; nothing is bound)")
def post_sidecars(payload: SidecarMatchRequest, drama_id: int = Path(ge=1)):
    return svc.rank_sidecar_names(drama_id, payload.media_name, payload.names)
