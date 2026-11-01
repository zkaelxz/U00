"""
api/routers/scanlate_routes.py -- the automatic Scanlate path (spec
docs/archive/scanlate-api-spec.md). Thin over
services/scanlate_pages_service.py, scanlate_run_service.py and
scanlate_render_service.py. Shares the /api/scanlate prefix with the comic
viewer's read routes (api/routers/comic_routes.py), which serve the page
list and the original/typeset images.

- GET  /dramas/{id}/config                  library.read
- GET  /dramas/{id}/pages/{pid}             lines.read   (regions carry text)
- PATCH /dramas/{id}/pages/{pid}/regions/{rid}  lines.edit (one text box, compare-and-set
                                              on the page rev; 409 when stale)
- POST /dramas/{id}/pages/{pid}/regions     lines.edit   (a new text box, same check)
- DELETE /dramas/{id}/pages/{pid}/regions/{rid}?expected_rev=  lines.edit (same check)
- POST /dramas/{id}/pages/{pid}/undo        lines.edit   (the page's latest box edit, add
                                              or delete)
- GET  /dramas/{id}/run-notes               library.read
- POST /dramas/{id}/pages                   media.upload (admin only; away from the PC only
                                              while the remote-uploads switch is on)
- POST /dramas/{id}/run                     jobs.start + require_engines_allowed
- POST /dramas/{id}/render                  jobs.start   (no engine)
- POST /dramas/{id}/export                  jobs.start   (ZIP/PDF artifacts)

Jobs are `scanlate_<drama_id>` (poll and cancel with /api/jobs). The
upload's Content-Length is checked before the body is read (chunked bodies
are refused) and the body stream itself is counted, like the cover upload.
"""

from fastapi import APIRouter, Path, Query, Request
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from api.auth import is_at_pc, require_engines_allowed, require_permission
from api.scanlate_schemas import (ScanlateRegionRedoRequest, ScanlateConfig, ScanlateExportRequest, ScanlateJobStarted,
                                  ScanlatePageDetail, ScanlateRenderRequest, ScanlateRunNotes,
                                  ScanlateRunRequest, ScanlateUploadResult)
from api.schemas import (ComicRegionCreateRequest, ComicRegionEditRequest,
                         ComicRegionUndoRequest, error_responses)
from lib.capped_request import read_capped_form
from services import (scanlate_region_redo_service, model_readiness_service, page_import_limits, scanlate_pages_service,
                      scanlate_render_service, scanlate_run_service)
from lib.errors import InvalidInputError

router = APIRouter(prefix="/api/scanlate", tags=["scanlate"])

_ERRS = error_responses(404, 422)
_JOB_ERRS = error_responses(400, 404, 409, 422, 503)


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


_EDIT_ERRS = error_responses(404, 409, 422)


# lines.edit like the comic viewer's other edits: a text box is the comic's
# equivalent of a line's English. Re-rendering stays a job (POST .../render).
@router.patch("/dramas/{drama_id}/pages/{page_id}/regions/{region_id}",
              dependencies=[require_permission("lines.edit")],
              response_model=ScanlatePageDetail, responses=_EDIT_ERRS,
              summary="Change one text box (English, box, kind, font, colour) if the page is still "
                      "at expected_rev; 409 with the current rev otherwise")
def patch_region(body: ComicRegionEditRequest, drama_id: int = Path(ge=1, le=2**31 - 1),
                 page_id: int = Path(ge=1, le=2**31 - 1),
                 region_id: int = Path(ge=1, le=2**31 - 1)):
    expected = body.expected.model_dump(exclude_unset=True) if body.expected else None
    return scanlate_pages_service.edit_region(
        drama_id, page_id, region_id, body.changes.model_dump(exclude_unset=True),
        body.expected_rev, expected)


@router.post("/dramas/{drama_id}/pages/{page_id}/regions",
             dependencies=[require_permission("lines.edit")],
             response_model=ScanlatePageDetail, responses=_EDIT_ERRS,
             summary="Add one text box inside the page if it is still at expected_rev; the "
                     "new box is the last region in the answer")
def post_region(body: ComicRegionCreateRequest, drama_id: int = Path(ge=1, le=2**31 - 1),
                page_id: int = Path(ge=1, le=2**31 - 1)):
    return scanlate_pages_service.create_region(
        drama_id, page_id, body.region.model_dump(exclude_unset=True), body.expected_rev)


# The rev is a query parameter: some clients and proxies drop a DELETE body.
@router.delete("/dramas/{drama_id}/pages/{page_id}/regions/{region_id}",
               dependencies=[require_permission("lines.edit")],
               response_model=ScanlatePageDetail, responses=_EDIT_ERRS,
               summary="Remove one text box if the page is still at expected_rev")
def delete_region(drama_id: int = Path(ge=1, le=2**31 - 1),
                  page_id: int = Path(ge=1, le=2**31 - 1),
                  region_id: int = Path(ge=1, le=2**31 - 1),
                  expected_rev: int = Query(ge=0, le=2**31 - 1)):
    return scanlate_pages_service.delete_region(drama_id, page_id, region_id, expected_rev)


@router.post("/dramas/{drama_id}/pages/{page_id}/regions/{region_id}/redo",
             dependencies=[require_permission("jobs.start")],
             response_model=ScanlatePageDetail, responses=_EDIT_ERRS,
             summary="Read one text box again and translate it again (mode both), or translate "
                     "its current source text again (mode translate), then re-render the page")
def post_region_redo(body: ScanlateRegionRedoRequest, request: Request,
                     drama_id: int = Path(ge=1, le=2**31 - 1), page_id: int = Path(ge=1, le=2**31 - 1),
                     region_id: int = Path(ge=1, le=2**31 - 1)):
    engine = body.engine or scanlate_pages_service.default_engine()
    require_engines_allowed(request, engine)
    return scanlate_region_redo_service.redo_region(
        drama_id, page_id, region_id, body.expected_rev, body.mode, engine)


@router.post("/dramas/{drama_id}/pages/{page_id}/undo",
             dependencies=[require_permission("lines.edit")],
             response_model=ScanlatePageDetail, responses=_EDIT_ERRS,
             summary="Undo the page's latest text-box edit, new box or deletion if nothing "
                     "else wrote the page since")
def post_undo(body: ComicRegionUndoRequest, drama_id: int = Path(ge=1, le=2**31 - 1),
              page_id: int = Path(ge=1, le=2**31 - 1)):
    return scanlate_pages_service.undo_region_edit(drama_id, page_id, body.expected_rev)


@router.get("/dramas/{drama_id}/run-notes",dependencies=[require_permission("library.read")],
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


@router.post("/dramas/{drama_id}/pages", dependencies=[require_permission("media.upload")],
             response_model=ScanlateUploadResult, openapi_extra=_UPLOAD_BODY,
             responses=error_responses(413, 409, 404, 422, 503),
             summary="Add page images or PDFs (PNG/JPEG/WebP/PDF; see config upload_limits)")
async def post_pages(request: Request, drama_id: int = Path(ge=1, le=2**31 - 1)):
    form = await read_capped_form(
        request, page_import_limits.MAX_IMPORT_BYTES + _MULTIPART_OVERHEAD, "the pages",
        _TOO_LARGE, "Send the pages as multipart/form-data 'files' parts (at most "
        f"{page_import_limits.MAX_FILES_PER_IMPORT}).",
        max_files=page_import_limits.MAX_FILES_PER_IMPORT, max_fields=1, max_part_size=1024)
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
    engine = body.engine or scanlate_pages_service.default_engine()
    require_engines_allowed(request, engine)
    model_readiness_service.require_ready(
        is_at_pc(request), model_readiness_service.for_scanlate, drama_id, body.detect_backend,
        body.art_captions)
    return scanlate_run_service.start_run(
        drama_id, mode=body.mode, page_id=body.page_id, confirm=body.confirm, engine=engine,
        detect_backend=body.detect_backend, chapter_id=body.chapter_id,
        art_captions=body.art_captions)


@router.post("/dramas/{drama_id}/render", dependencies=[require_permission("jobs.start")],
             response_model=ScanlateJobStarted, responses=_JOB_ERRS,
             summary="Re-render the typeset image of one page, or of every page with regions")
def post_render(body: ScanlateRenderRequest, request: Request,
                drama_id: int = Path(ge=1, le=2**31 - 1)):
    model_readiness_service.require_ready(
        is_at_pc(request), model_readiness_service.for_scanlate_render)
    return scanlate_render_service.start_render(drama_id, page_id=body.page_id)


@router.post("/dramas/{drama_id}/export", dependencies=[require_permission("jobs.start")],
             response_model=ScanlateJobStarted, responses=_JOB_ERRS,
             summary="Write the chapter as a ZIP and/or PDF (download from /api/artifacts, "
                     "kinds scanlate_zip / scanlate_pdf)")
def post_export(body: ScanlateExportRequest, request: Request,
                drama_id: int = Path(ge=1, le=2**31 - 1)):
    model_readiness_service.require_ready(
        is_at_pc(request), model_readiness_service.for_scanlate_render)
    return scanlate_render_service.start_export(drama_id, formats=body.formats, typeset=body.typeset)
