"""
api/routers/quick_video_routes.py -- Quick video translate: a link and a saved profile in, a
subtitled title out. Thin: see services/quick_video_service.py.

Starting and removing are PC only like the yt-dlp download they run.
"""

from fastapi import APIRouter, Path, Request

from api.auth import holds_paid_engines, is_at_pc, local_only, require_permission
from api.schemas import (DeleteConfirm, QuickVideoRuns, QuickVideoSettings, QuickVideoSettingsSet,
                         QuickVideoStart, QuickVideoStarted, error_responses)
from lib.ids import MAX_ID
from services import quick_video_service as svc

router = APIRouter(prefix="/api/quick-video", tags=["quick-video"])


@router.get("/settings", dependencies=[require_permission("library.read")], response_model=QuickVideoSettings,
            summary="The profile Quick video translate uses, and the profiles to pick from")
def get_settings():
    return svc.get_settings()


@router.post("/settings", dependencies=[require_permission("admin.library")], response_model=QuickVideoSettings,
             summary="Choose the profile Quick video translate uses (null clears it)",
             responses=error_responses(404, 422))
def set_settings(body: QuickVideoSettingsSet):
    return svc.set_profile(body.profile_id)


@router.post("/start", dependencies=[local_only()], response_model=QuickVideoStarted,
             summary="Create a title for a video link and queue download, transcribe, translate and export",
             description="PC only. A refused request creates nothing. Follow the run with GET /api/quick-video/runs.",
             responses=error_responses(403, 409, 422, 503))
def start(body: QuickVideoStart, request: Request):
    return svc.start(request.state.principal, body.url, body.profile_id, body.watch_once,
                     holds_paid=holds_paid_engines(request), at_pc=is_at_pc(request))


@router.get("/runs", dependencies=[require_permission("jobs.start")], response_model=QuickVideoRuns,
            summary="Recent Quick video translate runs and how far each got")
def get_runs(request: Request):
    return {"items": svc.runs(request.state.principal)}


@router.post("/{drama_id}/delete", dependencies=[local_only()],
             summary="Remove a watch-once title (and only that)",
             responses=error_responses(404, 409, 422))
def delete_temporary(body: DeleteConfirm, request: Request, drama_id: int = Path(ge=1, le=MAX_ID)):
    return svc.delete_temporary(request.state.principal, drama_id, confirm=body.confirm)
