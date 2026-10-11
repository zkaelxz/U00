"""
api/routers/scanlate_routes.py -- the automatic Scanlate path (spec
docs/archive/scanlate-api-spec.md). Thin over
services/scanlate_pages_service.py, scanlate_run_service.py and
scanlate_render_service.py. Shares the /api/scanlate prefix with the comic
viewer's read routes (api/routers/comic_routes.py), which serve the page
list and the original/typeset images.

- GET  /dramas/{id}/config                  library.read
- GET  /dramas/{id}/pages/{pid}             lines.read   (regions carry text)
- GET  /dramas/{id}/run-notes               library.read
- POST /dramas/{id}/pages                   local_only() (uploads are PC-only)
- POST /dramas/{id}/run                     jobs.start + require_engines_allowed
- POST /dramas/{id}/render                  jobs.start   (no engine)
- POST /dramas/{id}/export                  jobs.start   (ZIP/PDF artifacts)

Jobs are `scanlate_<drama_id>` (poll and cancel with /api/jobs). The
upload's Content-Length is checked before the body is read (chunked bodies
are refused) and the body stream itself is counted, like the cover upload.
"""

from fastapi import APIRouter, Path, Request
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException

from api.auth import local_only, require_engines_allowed, require_permission
from api.routers.bug_report_routes import BodyTooLarge, capped
from api.scanlate_schemas import (ScanlateConfig, ScanlateExportRequest, ScanlateJobStarted,
                                  ScanlatePageDetail, ScanlateRenderRequest, ScanlateRunNotes,
                                  ScanlateRunRequest, ScanlateUploadResult)
from api.schemas import ErrorResponse
from services import (page_import_limits, scanlate_pages_service, scanlate_render_service,
                      scanlate_run_service, settings_service)
from services.service_errors import InvalidInputError

router = APIRouter(prefix="/api/scanlate", tags=["scanlate"])

_ERRS = {404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}}
_JOB_ERRS = {400: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
             409: {"model": ErrorResponse}, 422: {"model": ErrorResponse},
             503: {"model": ErrorResponse}}


@router.get("/dramas/{drama_id}/config", dependencies=[require_permission("library.read")],
            response_model=ScanlateConfig, responses=_ERRS,
            summary="Scanlate panel config: engines (key booleans only), models cached, "
                    "OCR backend, upload limits, job state")
def get_config(drama_id: int = Path(ge=1, le=2**31 - 1)):
    return scanlate_pages_service.get_config(drama_id)


@router.get("/dramas/{drama_id}/pages/{page_id}", dependencies=[require_permission("lines.read")],
            response_model=ScanlatePageDetail, responses=_ERRS,
            summary="One page: its regions by stable id, rev and last run notes (no paths)")
def get_page(drama_id: int = Path(ge=1, le=2**31 - 1), page_id: int = Path(ge=1, le=2**31 - 1)):
    return scanlate_pages_service.get_page_detail(drama_id, page_id)


@router.get("/dramas/{drama_id}/run-notes", dependencies=[require_permission("library.read")],
            response_model=ScanlateRunNotes, responses=_ERRS,
            summary="Every page's notes from the last run (redacted, no paths)")
def get_run_notes(drama_id: int = Path(ge=1, le=2**31 - 1)):
    return scanlate_pages_service.list_run_notes(drama_id)


_MULTIPART_OVERHEAD = 1024 * 1024
_TOO_LARGE = "The upload is too large."
_UPLOAD_BODY = {"requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
    "type": "object", "required": ["files"],
    "properties": {"files": {"type": "array", "items": {"type": "string", "format": "binary"}},
                   "slice_strips": {"type": "boolean"}}}}}}}


@router.post("/dramas/{drama_id}/pages", dependencies=[local_only()],
             response_model=ScanlateUploadResult, openapi_extra=_UPLOAD_BODY,
             responses={413: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
                        **_ERRS, 503: {"model": ErrorResponse}},
             summary="Add page images or PDFs (PNG/JPEG/WebP/PDF; see config upload_limits)")
async def post_pages(request: Request, drama_id: int = Path(ge=1, le=2**31 - 1)):
    if "transfer-encoding" in request.headers:
        raise InvalidInputError("Send the pages with a Content-Length, not chunked.")
    try:
        length = int(request.headers.get("content-length", ""))
    except ValueError:
        raise InvalidInputError("An upload needs a Content-Length.")
    cap = page_import_limits.MAX_IMPORT_BYTES + _MULTIPART_OVERHEAD
    if length > cap:
        raise StarletteHTTPException(413, _TOO_LARGE)
    try:
        form = await capped(request, cap).form(
            max_files=page_import_limits.MAX_FILES_PER_IMPORT, max_fields=1,
            max_part_size=1024)
    except BodyTooLarge:
        raise StarletteHTTPException(413, _TOO_LARGE)
    except (MultiPartException, StarletteHTTPException):
        raise InvalidInputError(
            "Send the pages as multipart/form-data 'files' parts (at most "
            f"{page_import_limits.MAX_FILES_PER_IMPORT}).") from None
    try:
        uploads = [u for u in form.getlist("files") if isinstance(u, UploadFile)]
        if len(uploads) != len(form.getlist("files")) or not uploads:
            raise InvalidInputError("Send the pages as multipart/form-data 'files' parts.")
        raw = form.get("slice_strips")
        if raw is None:
            slice_strips = scanlate_pages_service.SLICE_STRIPS_DEFAULT
        elif isinstance(raw, str) and raw.lower() in ("true", "1", "false", "0"):
            slice_strips = raw.lower() in ("true", "1")
        else:
            raise InvalidInputError("slice_strips must be true or false.")
        return await run_in_threadpool(
            scanlate_pages_service.add_page_images, drama_id,
            [(u.filename, u.file) for u in uploads], slice_strips)
    finally:
        await form.close()


@router.post("/dramas/{drama_id}/run", dependencies=[require_permission("jobs.start")],
             response_model=ScanlateJobStarted, responses=_JOB_ERRS,
             summary="Detect, OCR, translate and typeset pages as one job "
                     "(missing = skip pages with regions; page = redo one; all = redo every page)")
def post_run(body: ScanlateRunRequest, request: Request, drama_id: int = Path(ge=1, le=2**31 - 1)):
    # The configured default is resolved first, so a household user can run
    # a drama whose default engine is a free one.
    engine = body.engine or settings_service.get("default_engine")
    require_engines_allowed(request, engine)
    return scanlate_run_service.start_run(
        drama_id, mode=body.mode, page_id=body.page_id, confirm=body.confirm, engine=engine,
        detect_backend=body.detect_backend, chapter_id=body.chapter_id)


@router.post("/dramas/{drama_id}/render", dependencies=[require_permission("jobs.start")],
             response_model=ScanlateJobStarted, responses=_JOB_ERRS,
             summary="Re-render the typeset image of one page, or of every page with regions")
def post_render(body: ScanlateRenderRequest, drama_id: int = Path(ge=1, le=2**31 - 1)):
    return scanlate_render_service.start_render(drama_id, page_id=body.page_id)


@router.post("/dramas/{drama_id}/export", dependencies=[require_permission("jobs.start")],
             response_model=ScanlateJobStarted, responses=_JOB_ERRS,
             summary="Write the chapter as a ZIP and/or PDF (download from /api/artifacts, "
                     "kinds scanlate_zip / scanlate_pdf)")
def post_export(body: ScanlateExportRequest, drama_id: int = Path(ge=1, le=2**31 - 1)):
    return scanlate_render_service.start_export(drama_id, formats=body.formats)
