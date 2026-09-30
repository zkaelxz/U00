"""
api/routers/assistant_routes.py -- the in-app AI maintenance assistant
(roadmap Step 42, read-only v1). Thin: see
services/maintenance_assistant_service.py.

Every route is `local_only()`: the assistant reads the app's code, git
history, redacted log and diagnostics, and its settings and backlog are
PC-side state, so none of it is reachable from another device. Asking and
the changelog also need Developer Mode on (409 otherwise). No route
applies a change: a proposed fix comes back as patch text for the user to
review and apply by hand.
"""

from fastapi import APIRouter, Path

from api.assistant_schemas import (AssistantAnswer, AssistantAskRequest, AssistantBacklog,
                                   AssistantBacklogAdd, AssistantBacklogCleared,
                                   AssistantBacklogDeleted, AssistantBacklogItem,
                                   AssistantChangelog, AssistantChangelogRequest,
                                   AssistantConfirm, AssistantReport,
                                   AssistantReportRequest, AssistantSettings,
                                   AssistantSettingsUpdate, AssistantTools)
from api.auth import local_only
from api.schemas import ErrorResponse
from services import maintenance_assistant_service as svc

router = APIRouter(prefix="/api/assistant", tags=["assistant"])

_ERRS = {404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
         422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}}


@router.get("/settings", dependencies=[local_only()], response_model=AssistantSettings,
            summary="PC only: Developer Mode and the assistant's default engine")
def get_settings():
    return svc.get_settings()


@router.post("/settings", dependencies=[local_only()], response_model=AssistantSettings,
             summary="PC only: turn Developer Mode on/off, set the default engine/model",
             responses=_ERRS)
def post_settings(body: AssistantSettingsUpdate):
    return svc.set_settings(body.model_dump(exclude_unset=True))


@router.get("/tools", dependencies=[local_only()], response_model=AssistantTools,
            summary="PC only: the assistant's tools (all read-only)")
def get_tools():
    return svc.list_tools()


@router.post("/ask", dependencies=[local_only()], response_model=AssistantAnswer,
             summary="PC only: ask the maintenance assistant (read-only tools; a fix comes "
                     "back as a patch, never applied). 409 when Developer Mode is off",
             responses=_ERRS)
def post_ask(body: AssistantAskRequest):
    return svc.ask(body.question, [t.model_dump() for t in body.chat_history],
                   engine_name=body.engine, model=body.model, escalate=body.escalate,
                   consent=body.consent, evidence=body.evidence)


@router.post("/report", dependencies=[local_only()], response_model=AssistantReport,
             summary="PC only: a redacted problem report (the chat, tool output and the "
                     "support report) for the user to copy to a developer. Nothing is uploaded",
             responses=_ERRS)
def post_report(body: AssistantReportRequest):
    return svc.developer_report([t.model_dump() for t in body.chat_history],
                                question=body.question, evidence=body.evidence)


@router.post("/changelog", dependencies=[local_only()], response_model=AssistantChangelog,
             summary="PC only: plain-English release notes for a commit range",
             responses=_ERRS)
def post_changelog(body: AssistantChangelogRequest):
    return svc.changelog(body.from_ref, body.to_ref, engine_name=body.engine, model=body.model)


@router.get("/backlog", dependencies=[local_only()], response_model=AssistantBacklog,
            summary="PC only: the assistant's project backlog, newest first")
def get_backlog():
    return svc.list_backlog()


@router.post("/backlog", dependencies=[local_only()], response_model=AssistantBacklogItem,
             summary="PC only: add a backlog item", responses=_ERRS)
def post_backlog(body: AssistantBacklogAdd):
    return svc.add_backlog_item(body.kind, body.text)


@router.post("/backlog/clear", dependencies=[local_only()],
             response_model=AssistantBacklogCleared,
             summary="PC only: delete every backlog item (confirm=true)", responses=_ERRS)
def post_backlog_clear(body: AssistantConfirm):
    return svc.clear_backlog(confirm=body.confirm)


@router.post("/backlog/{backlog_id}/delete", dependencies=[local_only()],
             response_model=AssistantBacklogDeleted,
             summary="PC only: delete one backlog item (confirm=true)", responses=_ERRS)
def post_backlog_delete(body: AssistantConfirm, backlog_id: int = Path(ge=1)):
    return svc.delete_backlog_item(backlog_id, confirm=body.confirm)
