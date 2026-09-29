"""
api/routers/lines_routes.py -- the Review stage's per-line WRITES for one
drama (Migration Slice 43): partial line edit (with compare-and-set on the
client's old values), dismiss flag, find-and-replace apply, translation-memory
accept, and translation-note add/delete.

Lines are addressed by permanent line id, never by position, and every write
is field-scoped (never a full line-list sync). Writes are POSTs; a note delete
is a DELETE with no confirm, matching the Review tab.

Auto-shorten overlong lines (review parity R28) calls an LLM, so besides
`lines.edit` the handler runs `require_engines_allowed` on the engine the
call will use (the named one, else the drama's own) and takes an LLM
slot; it writes only `en` (compare-and-set per line) after a line_history
snapshot, needs confirm=true and is refused (409) while a job runs for the
drama (services/line_tools_service.py).
"""

from fastapi import APIRouter, Path, Request
from api.auth import require_engines_allowed, require_permission
from api.llm_slots import llm_slot
from api.schemas import (ErrorResponse, LinesAcceptTmRequest, LinesFindReplaceApplyRequest,
                         LinesFindReplaceApplyResult, LinesNote, LinesNoteCreate,
                         LinesNoteDeleteResult, LinesPatchRequest, LinesShortenRequest,
                         LinesShortenResult, ReviewLinesLine)
from services import line_ai_service, line_tools_service, lines_service

router = APIRouter(prefix="/api/lines", tags=["lines"])

_404_422 = {404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}}
_404_409_422 = {**_404_422, 409: {"model": ErrorResponse}}


@router.post("/dramas/{drama_id}/lines/{line_id}", dependencies=[require_permission("lines.edit")], response_model=ReviewLinesLine,
             summary="Edit some fields of one line (409 if `expected` old values differ)",
             responses=_404_409_422)
def post_patch_line(body: LinesPatchRequest, drama_id: int = Path(ge=1), line_id: int = Path(ge=1)):
    return lines_service.patch_line(drama_id, line_id, **body.model_dump(exclude_unset=True))


@router.post("/dramas/{drama_id}/lines/{line_id}/dismiss-flag", dependencies=[require_permission("lines.edit")], response_model=ReviewLinesLine,
             summary="Clear one line's flag and flag note", responses=_404_422)
def post_dismiss_flag(drama_id: int = Path(ge=1), line_id: int = Path(ge=1)):
    return lines_service.dismiss_flag(drama_id, line_id)


@router.post("/dramas/{drama_id}/find-replace/apply", dependencies=[require_permission("lines.edit")], response_model=LinesFindReplaceApplyResult,
             summary="Apply previewed find-and-replace matches; stale ones are skipped",
             responses=_404_422)
def post_find_replace_apply(body: LinesFindReplaceApplyRequest, drama_id: int = Path(ge=1)):
    return lines_service.apply_find_replace(drama_id, [m.model_dump() for m in body.matches])


@router.post("/dramas/{drama_id}/lines/{line_id}/accept-tm", dependencies=[require_permission("lines.edit")], response_model=ReviewLinesLine,
             summary="Accept a translation-memory suggestion for one line (409 if its English changed)",
             responses=_404_409_422)
def post_accept_tm(body: LinesAcceptTmRequest, drama_id: int = Path(ge=1),
                   line_id: int = Path(ge=1)):
    return lines_service.accept_tm_suggestion(drama_id, line_id, body.entry_id, body.expected_en)


@router.post("/dramas/{drama_id}/notes", dependencies=[require_permission("lines.edit")], response_model=LinesNote,
             summary="Add (or update, for the same line+term) a translation note",
             responses=_404_422)
def post_add_note(body: LinesNoteCreate, drama_id: int = Path(ge=1)):
    return lines_service.add_note(drama_id, body.line_id, body.term, body.note_type, body.note)


@router.delete("/dramas/{drama_id}/notes/{note_id}", dependencies=[require_permission("lines.edit")], response_model=LinesNoteDeleteResult,
               summary="Delete one translation note (no confirm, as in the tab)",
               responses={404: {"model": ErrorResponse}})
def delete_note(drama_id: int = Path(ge=1), note_id: int = Path(ge=1)):
    return lines_service.delete_note(drama_id, note_id)


@router.post("/dramas/{drama_id}/shorten-overlong", dependencies=[require_permission("lines.edit")],
             response_model=LinesShortenResult,
             summary="Rewrite lines too long for their time slot more concisely (LLM)",
             responses={**_404_409_422, 403: {"model": ErrorResponse}, 429: {"model": ErrorResponse},
                        503: {"model": ErrorResponse}})
def post_shorten_overlong(body: LinesShortenRequest, request: Request, drama_id: int = Path(ge=1)):
    engine = line_ai_service.tool_engine_name(drama_id, body.engine)
    require_engines_allowed(request, engine)
    with llm_slot(request):
        return line_tools_service.shorten_overlong(drama_id, body.line_ids, engine,
                                                   body.model, body.gemini_free_tier,
                                                   confirm=body.confirm)
