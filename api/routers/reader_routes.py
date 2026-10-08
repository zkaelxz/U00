"""
api/routers/reader_routes.py -- the Reader's API (prefix /api/reader).

The page HTML route was added later (GET .../page), served for a
sandboxed iframe from cached definitions only. The rest of the Reader is over
`services/reader_service.py`
(its module docstring holds the permission contract, decided by the user
on 2026-09-29):

- reads (overview, notes, media availability, vocab list, wiki list):
  `library.read`;
- caption tracks and every export (vocab CSV,
  .apkg, wiki Markdown): `lines.read`;
- reader-data writes (progress, notes, lookup, rich-export queue, clear
  wiki): `lines.edit`;
- LLM tools (who-is, explain, recap, relationships, wiki update, Q&A):
  `jobs.start` plus `require_engines_allowed(request, body.engine)`; an
  omitted engine means Claude and counts as paid. The lookup route is
  `lines.edit` and, only with `use_llm`, also needs `jobs.start` and the
  engine check.

LLM routes are synchronous, as the service is: the request waits for
the engine. Because each one holds a server worker thread for as long as
the engine takes, at most LLM_MAX_IN_FLIGHT of them (the six LLM routes,
the lookup with use_llm, and the rich .apkg export, which cuts audio
with ffmpeg) run at once server-wide, and at most one per caller (user
id, or "local" with auth off); a request over either cap gets 429
`rate_limited` at once rather than queueing (the pool, api/llm_slots.py, is
shared with the Review blocked-line retry). The service bounds the work
inside one request (recap input, wiki chunks per call, rich cards and
ffmpeg time). The rich .apkg only embeds audio clips for a caller holding
`media.stream` (media bytes need it); otherwise the deck is text-only and
the response carries `X-Audio-Omitted: true`. Downloads use generic ASCII names (`drama_<id>_...`), never
the drama title or a path. Media files themselves are played through
/api/media and /api/dub; `media_file_path` is not exposed here, nor is
the series glossary (the glossary routes cover it).
"""

from typing import Literal, Optional

from fastapi import APIRouter, Path, Query, Request, Response
from api import llm_slots
from api.auth import require_engines_allowed, require_permission
from api.llm_slots import LLM_MAX_IN_FLIGHT, ACTIVE_CALLERS, ACTIVE_LOCK, SLOTS  # noqa: F401 -- re-exported for tests
from api.schemas import (ErrorResponse, ReaderAnswer, ReaderAskRequest, ReaderExplainRequest,
                         ReaderLookupRequest, ReaderLookupResult, ReaderMediaAvailability,
                         ReaderNotes, ReaderNotesRequest, ReaderOverview, ReaderPageResponse,
                         ReaderProgress, ReaderProgressRequest, ReaderRecap,
                         ReaderRecapRequest, ReaderRelationshipMap, ReaderRichExportRequest,
                         ReaderRichExportResult, ReaderScopedLlmRequest, ReaderVocabList,
                         ReaderWhoRequest, ReaderWikiClearRequest, ReaderWikiClearResult,
                         ReaderWikiList, ReaderWikiUpdateRequest, ReaderWikiUpdateResult)
from services import reader_service
from services.service_errors import ForbiddenError, NotFoundError

router = APIRouter(prefix="/api/reader", tags=["reader"])

Track = Literal["Source", "English", "Bilingual"]

_READ_ERRS = {404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}}
_LLM_ERRS = {400: {"model": ErrorResponse}, 403: {"model": ErrorResponse},
             404: {"model": ErrorResponse}, 422: {"model": ErrorResponse},
             429: {"model": ErrorResponse}, 500: {"model": ErrorResponse},
             503: {"model": ErrorResponse}}

# Long synchronous work (LLM calls, ffmpeg): global and per-caller caps, one
# pool shared with the other synchronous LLM routes (api/llm_slots.py).
_BUSY = "The reader's AI tools are busy; try again in a moment."


def _llm_slot(request: Request):
    return llm_slots.llm_slot(request, _BUSY)


def _holds(request: Request, permission: str) -> bool:
    principal = getattr(request.state, "principal", None) or {}
    return permission in principal.get("permissions", ())


def _download(content, media_type: str, filename: str) -> Response:
    return Response(content=content, media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"',
                             "X-Content-Type-Options": "nosniff"})


def _require_llm_allowed(request: Request, engine: Optional[str]):
    """For a route whose declared permission isn't jobs.start (the lookup):
    an LLM call still needs jobs.start, then the paid-engine check."""
    if not _holds(request, "jobs.start"):
        raise ForbiddenError("Not allowed.")
    require_engines_allowed(request, engine)


@router.get("/dramas/{drama_id}/page", dependencies=[require_permission("library.read")], response_model=ReaderPageResponse,
            summary="One page of a drama's Reader view, definitions from cache only",
            responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}})
def get_reader_page(
        drama_id: int = Path(ge=1),
        page: int = Query(1, ge=1),
        chapter_size: int = Query(reader_service.DEFAULT_CHAPTER_SIZE, ge=10, le=200,
                                  description="Lines per page, matching the Reader tab's own "
                                              "'Lines per page' control."),
        theme: str = Query("light", description="One of reader.py's THEMES keys."),
        font_size: int = Query(22, ge=10, le=48),
        line_height: float = Query(2.4, ge=1.0, le=4.0),
        max_width: int = Query(1200, ge=400, le=2400),
        font: str = Query("system", description="One of reader.py's FONT_STACKS keys.")):
    return reader_service.get_reader_page(
        drama_id, page=page, chapter_size=chapter_size, theme=theme, font_size=font_size,
        line_height=line_height, max_width=max_width, font=font)


# --- overview, progress, notes ----------------------------------------------

@router.get("/dramas/{drama_id}/overview", dependencies=[require_permission("library.read")], response_model=ReaderOverview,
            summary="Length, line count, progress and resume point", responses=_READ_ERRS)
def get_overview(drama_id: int = Path(ge=1)):
    return reader_service.get_reading_overview(drama_id)


@router.post("/dramas/{drama_id}/progress", dependencies=[require_permission("lines.edit")], response_model=ReaderProgress,
             summary="Record that a page was viewed (progress row only)", responses=_READ_ERRS)
def post_progress(body: ReaderProgressRequest, drama_id: int = Path(ge=1)):
    return reader_service.save_reading_position(drama_id, body.page, body.chapter_size)


@router.get("/dramas/{drama_id}/notes", dependencies=[require_permission("library.read")], response_model=ReaderNotes,
            summary="The drama's personal notes", responses=_READ_ERRS)
def get_notes(drama_id: int = Path(ge=1)):
    return reader_service.get_notes(drama_id)


@router.post("/dramas/{drama_id}/notes", dependencies=[require_permission("lines.edit")], response_model=ReaderNotes,
             summary="Replace the drama's personal notes", responses=_READ_ERRS)
def post_notes(body: ReaderNotesRequest, drama_id: int = Path(ge=1)):
    return reader_service.save_notes(drama_id, body.notes)


# --- watch / listen -----------------------------------------------------------

@router.get("/dramas/{drama_id}/media", dependencies=[require_permission("library.read")], response_model=ReaderMediaAvailability,
            summary="Which media and caption tracks exist (booleans and labels, no paths)",
            responses=_READ_ERRS)
def get_media(drama_id: int = Path(ge=1)):
    return reader_service.get_media_availability(drama_id)


@router.get("/dramas/{drama_id}/captions/{track}", dependencies=[require_permission("lines.read")],
            summary="One caption track as WebVTT, from the current lines",
            responses={200: {"content": {"text/vtt": {}}}, **_READ_ERRS})
def get_caption_track(track: Track, drama_id: int = Path(ge=1)):
    tracks = reader_service.get_caption_tracks(drama_id)["tracks"]
    if track not in tracks:
        raise NotFoundError(f"This drama has no {track} caption track.")
    return Response(content=tracks[track], media_type="text/vtt; charset=utf-8",
                    headers={"X-Content-Type-Options": "nosniff"})


# --- click-to-define and vocab -------------------------------------------------

@router.post("/dramas/{drama_id}/lookup", dependencies=[require_permission("lines.edit")], response_model=ReaderLookupResult,
             summary="Look up and save definitions for one page (LLM fallback only with use_llm)",
             responses=_LLM_ERRS)
def post_lookup(body: ReaderLookupRequest, request: Request, drama_id: int = Path(ge=1)):
    if not body.use_llm:
        return reader_service.lookup_page_definitions(drama_id, body.page, body.chapter_size)
    _require_llm_allowed(request, body.engine)
    with _llm_slot(request):
        return reader_service.lookup_page_definitions(
            drama_id, body.page, body.chapter_size, use_llm=True,
            engine_name=body.engine, model=body.model)


@router.get("/dramas/{drama_id}/vocab", dependencies=[require_permission("library.read")], response_model=ReaderVocabList,
            summary="This drama's looked-up words", responses=_READ_ERRS)
def get_vocab(drama_id: int = Path(ge=1), rich_only: bool = Query(False)):
    return reader_service.list_vocab(drama_id, rich_only=rich_only)


@router.post("/dramas/{drama_id}/vocab/rich", dependencies=[require_permission("lines.edit")], response_model=ReaderRichExportResult,
             summary="Queue or un-queue words for the rich sentence Anki export",
             responses=_READ_ERRS)
def post_vocab_rich(body: ReaderRichExportRequest, drama_id: int = Path(ge=1)):
    return reader_service.set_rich_export(drama_id, body.words, queued=body.queued)


@router.get("/dramas/{drama_id}/vocab/export.csv", dependencies=[require_permission("lines.read")],
            summary="Anki-importable vocab CSV (download)",
            responses={200: {"content": {"text/csv": {}}}, **_READ_ERRS})
def get_vocab_csv(drama_id: int = Path(ge=1)):
    out = reader_service.export_vocab_csv(drama_id)
    return _download(out["content"], "text/csv; charset=utf-8", f"drama_{drama_id}_vocab.csv")


@router.get("/dramas/{drama_id}/vocab/export.apkg", dependencies=[require_permission("lines.read")],
            summary="Anki deck (download); rich=true builds the queued sentence cards "
                    "(audio clips only with media.stream)",
            responses={200: {"content": {"application/octet-stream": {}}},
                       429: {"model": ErrorResponse}, 503: {"model": ErrorResponse},
                       **_READ_ERRS})
def get_vocab_apkg(request: Request, drama_id: int = Path(ge=1), rich: bool = Query(False)):
    name = f"drama_{drama_id}_vocab_sentence.apkg" if rich else f"drama_{drama_id}_vocab.apkg"
    if not rich:
        out = reader_service.export_vocab_apkg(drama_id)
        return _download(out["content"], "application/octet-stream", name)
    with _llm_slot(request):
        out = reader_service.export_vocab_apkg(
            drama_id, rich=True, include_audio=_holds(request, "media.stream"))
    resp = _download(out["content"], "application/octet-stream", name)
    if out.get("audio_omitted"):
        resp.headers["X-Audio-Omitted"] = "true"
    if out.get("cards_capped"):
        resp.headers["X-Cards-Capped"] = str(reader_service.MAX_RICH_CARDS)
    return resp


# --- story tools (LLM, synchronous) ---------------------------------------------

@router.post("/dramas/{drama_id}/story/who", dependencies=[require_permission("jobs.start")], response_model=ReaderAnswer,
             summary="Who is this character (spoiler-scoped when up_to_line_idx is set)",
             responses=_LLM_ERRS)
def post_story_who(body: ReaderWhoRequest, request: Request, drama_id: int = Path(ge=1)):
    require_engines_allowed(request, body.engine)
    with _llm_slot(request):
        return reader_service.who_is_character(drama_id, body.name, body.up_to_line_idx,
                                               engine_name=body.engine, model=body.model)


@router.post("/dramas/{drama_id}/story/explain", dependencies=[require_permission("jobs.start")], response_model=ReaderAnswer,
             summary="Explain a reference or phrase", responses=_LLM_ERRS)
def post_story_explain(body: ReaderExplainRequest, request: Request, drama_id: int = Path(ge=1)):
    require_engines_allowed(request, body.engine)
    with _llm_slot(request):
        return reader_service.explain_reference(drama_id, body.phrase, body.up_to_line_idx,
                                                engine_name=body.engine, model=body.model)


@router.post("/dramas/{drama_id}/story/recap", dependencies=[require_permission("jobs.start")], response_model=ReaderRecap,
             summary="Recap what came before a page", responses=_LLM_ERRS)
def post_story_recap(body: ReaderRecapRequest, request: Request, drama_id: int = Path(ge=1)):
    require_engines_allowed(request, body.engine)
    with _llm_slot(request):
        return reader_service.recap(drama_id, body.page, body.chapter_size,
                                    engine_name=body.engine, model=body.model)


@router.post("/dramas/{drama_id}/story/relationships", dependencies=[require_permission("jobs.start")], response_model=ReaderRelationshipMap,
             summary="Character relationship map (with Mermaid text)", responses=_LLM_ERRS)
def post_story_relationships(body: ReaderScopedLlmRequest, request: Request,
                             drama_id: int = Path(ge=1)):
    require_engines_allowed(request, body.engine)
    with _llm_slot(request):
        return reader_service.relationship_map(drama_id, body.up_to_line_idx,
                                               engine_name=body.engine, model=body.model)


# --- universe wiki ---------------------------------------------------------------

@router.get("/dramas/{drama_id}/wiki", dependencies=[require_permission("library.read")], response_model=ReaderWikiList,
            summary="Wiki entries (hidden past up_to_line_idx; omitted = no spoiler limit)",
            responses=_READ_ERRS)
def get_wiki(drama_id: int = Path(ge=1),
             up_to_line_idx: Optional[int] = Query(None, ge=0),
             entry_type: Optional[str] = Query(None, max_length=40)):
    return reader_service.list_wiki(drama_id, up_to_line_idx, entry_type)


@router.post("/dramas/{drama_id}/wiki/update", dependencies=[require_permission("jobs.start")], response_model=ReaderWikiUpdateResult,
             summary="Extract wiki entries from the lines up to the boundary (LLM)",
             responses=_LLM_ERRS)
def post_wiki_update(body: ReaderWikiUpdateRequest, request: Request, drama_id: int = Path(ge=1)):
    require_engines_allowed(request, body.engine)
    with _llm_slot(request):
        return reader_service.update_wiki(drama_id, body.up_to_line_idx,
                                          engine_name=body.engine, model=body.model,
                                          from_line_idx=body.from_line_idx)


@router.post("/dramas/{drama_id}/wiki/clear", dependencies=[require_permission("lines.edit")], response_model=ReaderWikiClearResult,
             summary="Delete every wiki entry for the drama (needs confirm=true)",
             responses=_READ_ERRS)
def post_wiki_clear(body: ReaderWikiClearRequest, drama_id: int = Path(ge=1)):
    return reader_service.clear_wiki(drama_id, confirm=body.confirm)


@router.get("/dramas/{drama_id}/wiki/export.md", dependencies=[require_permission("lines.read")],
            summary="The shown wiki entries as Markdown (download)",
            responses={200: {"content": {"text/markdown": {}}}, **_READ_ERRS})
def get_wiki_markdown(drama_id: int = Path(ge=1),
                      up_to_line_idx: Optional[int] = Query(None, ge=0),
                      entry_type: Optional[str] = Query(None, max_length=40)):
    out = reader_service.export_wiki_markdown(drama_id, up_to_line_idx, entry_type)
    return _download(out["content"], "text/markdown; charset=utf-8",
                     f"drama_{drama_id}_universe_wiki.md")


# --- Q&A -------------------------------------------------------------------------

@router.post("/dramas/{drama_id}/ask", dependencies=[require_permission("jobs.start")], response_model=ReaderAnswer,
             summary="One grounded Q&A turn (stateless; the client sends the history)",
             responses=_LLM_ERRS)
def post_ask(body: ReaderAskRequest, request: Request, drama_id: int = Path(ge=1)):
    require_engines_allowed(request, body.engine)
    history = [t.model_dump() for t in body.chat_history]
    with _llm_slot(request):
        return reader_service.ask_about_drama(drama_id, body.question, history,
                                              engine_name=body.engine, model=body.model)
