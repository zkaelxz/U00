"""
api/routers/queue_routes.py -- the user-managed job queue on the Jobs page.
Thin: see services/queue_service.py.

Every route needs `jobs.start`. The service adds the rest: an item's title
must be the caller's, a Translate item on a paid engine needs `engines.paid`
(as the Translate stage's run does), a member sees and changes only items
for their own titles, and only the PC owner may start, pause or schedule.
"""

from typing import Literal, Optional

from fastapi import APIRouter, Path, Query, Request

from api.auth import (engine_for_caller, holds, holds_paid_engines, is_at_pc, is_local_request,
                      require_cloud_model_allowed, require_engines_allowed, require_permission)
from api.schemas import (QueueAddAllSteps, QueueAddBulk, QueueAddItem, QueueBulkAdded, QueueMove, QueuePolicy,
                         FileRunStart, NovelFlowStart, QueueScheduleSet, QueueState, QueueStepSettings, TitleFlowRun, error_responses)
from services import (glossary_extract_service, model_readiness_service, page_run_service, queue_service,
                      scanlate_pages_service, translate_run_service)

router = APIRouter(prefix="/api/queue", tags=["queue"])

_ERRORS = error_responses(403, 404, 409, 422)
_ITEM = Path(ge=1, lt=2**63)


@router.get("", dependencies=[require_permission("jobs.start")], response_model=QueueState,
            summary="The job queue: run state, schedule and the items the caller can see")
def get_queue(request: Request):
    return queue_service.get_queue(request.state.principal)


@router.get("/settings", dependencies=[require_permission("jobs.start")], response_model=QueueStepSettings,
            summary="The saved settings a queued step would use for a title (read-only)", responses=_ERRORS)
def step_settings(request: Request, drama_id: int = Query(ge=1, lt=2**63),
                  step: Literal["transcribe", "translate", "export"] = Query(),
                  model: Optional[str] = Query(default=None, max_length=200),
                  batch_size: Optional[int] = Query(default=None, ge=1, le=1000)):
    # Translate only: the model and batch size an item would change, so its cost line prices them. A hosted
    # Ollama tag is refused here as in the add, so the price check can't probe what the caller may not use.
    if model is not None and step == "translate":
        require_cloud_model_allowed(request, (None, model))
    given = {k: v for k, v in (("model", model), ("batch_size", batch_size)) if v is not None}
    return queue_service.step_settings(request.state.principal, drama_id, step, holds_paid_engines(request),
                                      holds(request, "lines.edit"), given if step == "translate" else None)


@router.post("/items", dependencies=[require_permission("jobs.start")], response_model=QueueState,
             summary="Add a title's step to the end of the queue", responses=_ERRORS)
def add_item(body: QueueAddItem, request: Request):
    return queue_service.add_item(request.state.principal, body.drama_id, body.step,
                                  holds_paid_engines(request), at_pc=is_at_pc(request),
                                  settings=body.settings, holds_edit=holds(request, "lines.edit"))


@router.post("/items/all-steps", dependencies=[require_permission("jobs.start")], response_model=QueueState,
             summary="Add Transcribe, Translate and Export for a title, in that order", responses=_ERRORS)
def add_all_steps(body: QueueAddAllSteps, request: Request):
    return queue_service.add_all_steps(request.state.principal, body.drama_id,
                                       holds_paid_engines(request), at_pc=is_at_pc(request))


@router.post("/items/bulk", dependencies=[require_permission("jobs.start")], response_model=QueueBulkAdded,
             summary="Add one step (or all three) for several titles; a refused title is skipped with its reason",
             responses=_ERRORS)
def add_bulk(body: QueueAddBulk, request: Request):
    return queue_service.add_bulk(request.state.principal, body.drama_ids, body.step,
                                  holds_paid_engines(request), at_pc=is_at_pc(request))


@router.post("/items/{item_id}/move", dependencies=[require_permission("jobs.start")],
             response_model=QueueState, summary="Move an item up or down", responses=_ERRORS)
def move_item(body: QueueMove, request: Request, item_id: int = _ITEM):
    return queue_service.move_item(request.state.principal, item_id, body.direction)


@router.post("/items/{item_id}/remove", dependencies=[require_permission("jobs.start")],
             response_model=QueueState, summary="Remove an item that is not running", responses=_ERRORS)
def remove_item(request: Request, item_id: int = _ITEM):
    return queue_service.remove_item(request.state.principal, item_id)


@router.post("/items/{item_id}/retry", dependencies=[require_permission("jobs.start")],
             response_model=QueueState, summary="Put a failed, skipped or interrupted item back to waiting",
             responses=_ERRORS)
def retry_item(request: Request, item_id: int = _ITEM):
    return queue_service.retry_item(request.state.principal, item_id, at_pc=is_at_pc(request))


@router.post("/clear-finished", dependencies=[require_permission("jobs.start")],
             response_model=QueueState, summary="Remove the caller's finished items", responses=_ERRORS)
def clear_finished(request: Request):
    return queue_service.clear_finished(request.state.principal)


@router.post("/start", dependencies=[require_permission("jobs.start")], response_model=QueueState,
             summary="Start (or resume) the queue now", responses=_ERRORS)
def start(request: Request):
    return queue_service.start(request.state.principal)


@router.post("/pause", dependencies=[require_permission("jobs.start")], response_model=QueueState,
             summary="Start no new item; a running one finishes", responses=_ERRORS)
def pause(request: Request):
    return queue_service.pause(request.state.principal)


@router.post("/schedule", dependencies=[require_permission("jobs.start")], response_model=QueueState,
             summary="Start the queue once at HH:MM on the PC's clock, or clear the schedule",
             responses=_ERRORS)
def set_schedule(body: QueueScheduleSet, request: Request):
    return queue_service.set_schedule(request.state.principal, body.time)


@router.post("/policy", dependencies=[require_permission("jobs.start")], response_model=QueueState,
             summary="Whether a failed item stops the queue, and whether a transcription may run beside an online translation", responses=_ERRORS)
def set_policy(body: QueuePolicy, request: Request):
    return queue_service.set_policy(request.state.principal, body.stop_on_failure, body.overlap_online_translate)


@router.get("/title-flow/{drama_id}", dependencies=[require_permission("jobs.start")], response_model=TitleFlowRun,
            summary="How far the title's one-button run (transcribe, then find names) got", responses=_ERRORS)
def get_title_flow(request: Request, drama_id: int = Path(ge=1, lt=2**63)):
    return page_run_service.title_flow(request.state.principal, drama_id)


@router.post("/title-flow/{drama_id}", dependencies=[require_permission("jobs.start")], response_model=TitleFlowRun,
             summary="Queue the title's one-button run to start now; a run already in line is returned",
             responses=error_responses(400, 403, 404, 409, 422, 503))
def start_title_flow(request: Request, drama_id: int = Path(ge=1, lt=2**63)):
    # The names step's engine is held to the same gate as the Glossary tab's own start.
    require_engines_allowed(request, glossary_extract_service.lines_glossary_engine(drama_id),
                            model=glossary_extract_service.glossary_model(drama_id))
    return page_run_service.start_title_flow(request.state.principal, drama_id, holds_paid_engines(request),
                                            at_pc=is_at_pc(request))


@router.get("/novel-flow/{drama_id}", dependencies=[require_permission("jobs.start")], response_model=TitleFlowRun,
            summary="How far the novel's link run (import, text, lines, then find names) got", responses=_ERRORS)
def get_novel_flow(request: Request, drama_id: int = Path(ge=1, lt=2**63)):
    return page_run_service.novel_flow(request.state.principal, drama_id)


@router.post("/novel-flow/{drama_id}", dependencies=[require_permission("jobs.start")], response_model=TitleFlowRun,
             summary="Queue the novel's link run to start now; a run already in line is returned",
             responses=error_responses(400, 403, 404, 409, 422, 503))
def start_novel_flow(body: NovelFlowStart, request: Request, drama_id: int = Path(ge=1, lt=2**63)):
    # Each step's engine is held to the gate of the button it replaces (narration run, Glossary tab).
    require_engines_allowed(request, glossary_extract_service.novel_glossary_engine(drama_id),
                            model=glossary_extract_service.glossary_model(drama_id))
    lines = page_run_service.lines_choice(drama_id, body.model_dump(exclude={"url"}))
    if "lines" in page_run_service.novel_steps(drama_id) and not lines["one_narrator"]:
        require_engines_allowed(request, lines["engine"], model=lines.get("model"))
    local = is_local_request(request)
    return page_run_service.start_novel_flow(
        request.state.principal, drama_id, body.url, lines, at_pc=is_at_pc(request), local=local,
        holds_import=holds(request, "sources.import"))


@router.get("/file-run/{drama_id}", dependencies=[require_permission("jobs.start")], response_model=TitleFlowRun,
            summary="How far the Add box run (the upload's transcription, then translate) got", responses=_ERRORS)
def get_file_run(request: Request, drama_id: int = Path(ge=1, lt=2**63)):
    return page_run_service.file_run(request.state.principal, drama_id)


@router.post("/file-run/{drama_id}", dependencies=[require_permission("jobs.start")], response_model=TitleFlowRun,
             summary="Queue the Add box run after its upload, to start now; a run already in line is returned",
             responses=error_responses(400, 403, 404, 409, 422))
def start_file_run(body: FileRunStart, request: Request, drama_id: int = Path(ge=1, lt=2**63)):
    # The same gates as the Translate stage's own start with this engine.
    require_engines_allowed(request, body.engine)
    require_cloud_model_allowed(request, (body.engine, None))
    return page_run_service.start_file_run(request.state.principal, drama_id, body.job_id, body.engine,
                                           body.locale, at_pc=is_at_pc(request))


@router.get("/after-import/{drama_id}", dependencies=[require_permission("jobs.start")], response_model=TitleFlowRun,
            summary="How far the translating that follows a chapter import got", responses=_ERRORS)
def get_after_import(request: Request, drama_id: int = Path(ge=1, lt=2**63)):
    return page_run_service.after_import_run(request.state.principal, drama_id)


@router.post("/after-import/{drama_id}", dependencies=[require_permission("jobs.start")], response_model=TitleFlowRun,
             summary="Queue the translating that follows the title's running chapter import",
             responses=error_responses(400, 403, 404, 409, 422, 503))
def start_after_import(request: Request, drama_id: int = Path(ge=1, lt=2**63)):
    """The engines are chosen here, never by the client: an engine the account may not use is swapped
    for the one every account may (`engine_for_caller`), and each step is held to its own gate."""
    kind = page_run_service.after_import_kind(request.state.principal, drama_id)
    at_pc = is_at_pc(request)
    if kind == "comic":
        default = scanlate_pages_service.default_engine()
        engine, _model = engine_for_caller(request, None, default, None)
        require_engines_allowed(request, engine)
        model_readiness_service.require_ready(at_pc, model_readiness_service.for_scanlate, drama_id, "auto", False)
        page_run_service.require_cap_for_after_import(engine, None)
        return page_run_service.start_after_import(request.state.principal, drama_id, engine, None,
                                                   at_pc=at_pc)
    default = translate_run_service.run_engine_name(drama_id)
    default_model = (translate_run_service.run_model_and_batch_size(drama_id, default)[0]
                     if default == "ollama" else None)
    engine, model = engine_for_caller(request, None, default, default_model)
    require_engines_allowed(request, engine, model=model)
    require_cloud_model_allowed(request, (engine, model))
    # Names and lines take the same server pick as the translation: no client choice, and an engine
    # the account may not use is swapped for the one every account may.
    names_engine, names_model = engine_for_caller(
        request, None, glossary_extract_service.novel_glossary_engine(drama_id),
        glossary_extract_service.glossary_model(drama_id))
    require_engines_allowed(request, names_engine, model=names_model)
    lines = page_run_service.lines_choice(drama_id, {})
    if not lines["one_narrator"]:
        lines["engine"], lines_model = engine_for_caller(request, None, lines["engine"], lines.get("model"))
        if lines_model:
            lines["model"] = lines_model
        require_engines_allowed(request, lines["engine"], model=lines.get("model"))
    page_run_service.require_cap_for_after_import(engine, model)
    return page_run_service.start_after_import(
        request.state.principal, drama_id, engine, lines, at_pc=at_pc, model=model,
        names={"engine": names_engine, **({"model": names_model} if names_model else {})})
