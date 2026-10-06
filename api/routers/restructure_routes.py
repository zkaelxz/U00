"""
api/routers/restructure_routes.py -- structural line changes for one drama:
add, delete, merge, split, re-segmentation
(read-only preview + a job that re-segments and saves; parity R47 adds an
LLM preview job, read back with GET .../resegment/preview-llm and applied
as shown with `use_preview: true`), and Version
history restore (the list is GET /api/review/dramas/{id}/history). Thin wrapper over services/restructure_service.py.

Every write carries `expected_line_ids` (409 when the drama's lines changed),
takes a history snapshot first, and is refused (409) while a job runs on the
drama. Deleting a line needs confirm=true; re-segmentation needs it when
translated/flagged/noted lines could be split.
"""
from fastapi import APIRouter, Path, Request
from api.auth import require_engines_allowed, require_permission
from api.schemas import (ErrorResponse, ResegmentLlmPreview, ResegmentLlmPreviewStart,
                         ResegmentPreview, ResegmentStart, ResegmentStarted,
                         RestoreVersionRequest, RestoreVersionResult, RestructureAddLine,
                         RestructureDeleteLine, RestructureMerge, RestructureResult,
                         RestructureSplit, ResplitResult, ResplitStart)
from services import restructure_service as svc

router = APIRouter(prefix="/api/restructure", tags=["restructure"])

_R = {404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}}


@router.post("/dramas/{drama_id}/lines/add", dependencies=[require_permission("lines.edit")], response_model=RestructureResult,
             summary="Insert a new line after another (or at the start)", responses=_R)
def post_add_line(body: RestructureAddLine, drama_id: int = Path(ge=1)):
    return svc.add_line(drama_id, body.expected_line_ids, after_line_id=body.after_line_id,
                        start=body.start, end=body.end, zh=body.zh, en=body.en,
                        speaker=body.speaker)


@router.post("/dramas/{drama_id}/lines/{line_id}/delete", dependencies=[require_permission("lines.edit")], response_model=RestructureResult,
             summary="Delete one line (confirm=true required)", responses=_R)
def post_delete_line(body: RestructureDeleteLine, drama_id: int = Path(ge=1),
                     line_id: int = Path(ge=1)):
    return svc.delete_line(drama_id, line_id, body.expected_line_ids, confirm=body.confirm)


@router.post("/dramas/{drama_id}/merge", dependencies=[require_permission("lines.edit")], response_model=RestructureResult,
             summary="Merge adjacent lines into the first", responses=_R)
def post_merge(body: RestructureMerge, drama_id: int = Path(ge=1)):
    return svc.merge_lines(drama_id, body.line_ids, body.expected_line_ids)


@router.post("/dramas/{drama_id}/lines/{line_id}/split", dependencies=[require_permission("lines.edit")], response_model=RestructureResult,
             summary="Split one line at a character (and optional time) offset", responses=_R)
def post_split(body: RestructureSplit, drama_id: int = Path(ge=1), line_id: int = Path(ge=1)):
    return svc.split_line(drama_id, line_id, body.expected_line_ids, at_char=body.at_char,
                          expected_zh=body.expected_zh, at_time=body.at_time,
                          en_at_char=body.en_at_char)


@router.get("/dramas/{drama_id}/resegment/preview", dependencies=[require_permission("lines.read")], response_model=ResegmentPreview,
            summary="Rule-based re-segmentation preview (read-only)",
            responses={404: {"model": ErrorResponse}})
def get_resegment_preview(drama_id: int = Path(ge=1)):
    return svc.preview_resegmentation(drama_id)


@router.post("/dramas/{drama_id}/resegment/preview-llm", dependencies=[require_permission("jobs.start")],
             response_model=ResegmentStarted,
             summary="Start an LLM re-segmentation preview job (writes no lines)",
             responses={**_R, 400: {"model": ErrorResponse}, 503: {"model": ErrorResponse}})
def post_resegment_llm_preview(body: ResegmentLlmPreviewStart, request: Request,
                               drama_id: int = Path(ge=1)):
    require_engines_allowed(request, body.engine)
    return svc.start_llm_resegment_preview(drama_id, engine=body.engine, model=body.model)


@router.get("/dramas/{drama_id}/resegment/preview-llm", dependencies=[require_permission("lines.read")],
            response_model=ResegmentLlmPreview,
            summary="The finished LLM re-segmentation preview (404 until one is ready)",
            responses={404: {"model": ErrorResponse}})
def get_resegment_llm_preview(drama_id: int = Path(ge=1)):
    return svc.get_llm_resegment_preview(drama_id)


@router.post("/dramas/{drama_id}/resegment", dependencies=[require_permission("lines.edit")], response_model=ResegmentStarted,
             summary="Start a job that re-segments and saves (poll /api/jobs/{job_id})",
             responses={**_R, 400: {"model": ErrorResponse}, 503: {"model": ErrorResponse}})
def post_resegment(body: ResegmentStart, request: Request, drama_id: int = Path(ge=1)):
    if body.use_llm:
        require_engines_allowed(request, body.engine)
    return svc.start_resegmentation(drama_id, body.expected_line_ids, confirm=body.confirm,
                                    use_llm=body.use_llm, engine=body.engine, model=body.model,
                                    use_preview=body.use_preview)


@router.post("/dramas/{drama_id}/resplit", dependencies=[require_permission("lines.edit")], response_model=ResplitResult,
             summary="Split over-long lines in place (estimated timing now, or an align-to-audio job)",
             responses={**_R, 400: {"model": ErrorResponse}})
def post_resplit(body: ResplitStart, drama_id: int = Path(ge=1)):
    return svc.resplit_long_lines(drama_id, body.expected_line_ids,
                                  align_to_audio=body.align_to_audio, confirm=body.confirm,
                                  sensitivity=body.sensitivity, max_seconds=body.max_seconds,
                                  dry_run=body.dry_run)


@router.post("/dramas/{drama_id}/history/{history_id}/restore", dependencies=[require_permission("lines.edit")],
             response_model=RestoreVersionResult,
             summary="Restore a snapshot (snapshots the current lines first)", responses=_R)
def post_restore(body: RestoreVersionRequest, drama_id: int = Path(ge=1),
                 history_id: int = Path(ge=1)):
    return svc.restore_version(drama_id, history_id, body.expected_line_ids)
