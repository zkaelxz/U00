"""
api/routers/sources_catalog_routes.py -- the Sources registry and status.
Prefix /api/sources. Deliberately a different file
name from source_routes.py, which is the Workspace Source stage
(/api/source/dramas/{id}/config).

Fixed-path routes (settings, profiles, tracked, notifications) are declared
before /{name} so they are never read as a source name.
"""

from typing import List

from fastapi import APIRouter, Path, Query, Request

from api.auth import is_local_request, local_only, require_permission
from api.schemas import (ErrorResponse, SourceAttempt, SourceCacheClearRequest,
                         SourceCacheStats, SourceDetail, SourceExtensionOnly,
                         SourceExtensionOnlyRequest, SourceHealth, SourceNotification,
                         SourceProfileDomain, SourceProfileRollbackRequest,
                         SourceProfileVersion, SourcesSettings, SourcesSettingsUpdate,
                         SourcePaceRequest, SourcesJobStarted, SourceSummary, SourceToggle,
                         SourceTrackedDramaRequest, SourceTrackedSaveRequest, SourceTrackRequest,
                         TrackedSeries)
from services import sources_registry_service as svc
from services import sources_tracking_service as tracking

router = APIRouter(prefix="/api/sources", tags=["sources"])

_NAME = Path(min_length=1, max_length=60)


@router.get("", dependencies=[require_permission("library.read")], response_model=List[SourceSummary], summary="List every source adapter")
def list_sources():
    return svc.list_sources()


@router.get("/settings", dependencies=[require_permission("admin.settings")], response_model=SourcesSettings,
            summary="Source pacing/cache settings (numbers and enums; proxy is a bool only)")
def get_settings():
    return svc.get_settings()


@router.get("/profiles", dependencies=[require_permission("admin.settings")], response_model=List[SourceProfileDomain],
            summary="Saved per-domain extraction profiles (version summaries only)")
def list_profiles():
    return svc.list_profiles()


@router.get("/tracked", dependencies=[require_permission("library.read")], response_model=List[TrackedSeries], summary="Tracked series")
def list_tracked(request: Request):
    return svc.list_tracked(principal=request.state.principal)


@router.get("/notifications", dependencies=[require_permission("library.read")], response_model=List[SourceNotification],
            summary="New-chapter notifications")
def list_notifications(include_dismissed: bool = False):
    return svc.list_notifications(include_dismissed)


@router.get("/{name}", dependencies=[require_permission("library.read")], response_model=SourceDetail,
            summary="One source's capability record, health and recorded terms (information only)",
            responses={404: {"model": ErrorResponse}})
def get_source(name: str = _NAME):
    return svc.get_source(name)


_ERR = {404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}}


# PC-only like every settings write: pacing, retries and concurrency decide
# how often this PC requests from each site (docs/remote-access-decision.md).
@router.post("/settings", dependencies=[local_only()], response_model=SourcesSettings,
             summary="Update whitelisted source settings (no proxy URL; pacing floor enforced)",
             responses=_ERR)
def post_settings(payload: SourcesSettingsUpdate):
    return svc.update_settings(payload.model_dump(exclude_unset=True))


@router.post("/cache/clear", dependencies=[require_permission("admin.settings")], response_model=SourceCacheStats,
             summary="Clear the raw-content cache (needs confirm=true)", responses=_ERR)
def post_cache_clear(payload: SourceCacheClearRequest):
    return svc.clear_cache(payload.confirm)


@router.post("/tracked", dependencies=[require_permission("sources.import")], response_model=List[TrackedSeries],
             summary="Track or untrack one series (fetches nothing)", responses=_ERR)
def post_tracked(payload: SourceTrackRequest, request: Request):
    return svc.set_tracked(payload.source, payload.series_id, payload.tracked, payload.title,
                           payload.url, payload.drama_id, principal=request.state.principal)


@router.post("/tracked/drama", dependencies=[require_permission("sources.import")], response_model=List[TrackedSeries],
             summary="Which drama a tracked series auto-imports into (null = none; fetches nothing)",
             responses=_ERR)
def post_tracked_drama(payload: SourceTrackedDramaRequest, request: Request):
    return tracking.set_tracked_drama(payload.source, payload.series_id, payload.drama_id,
                                      principal=request.state.principal)


@router.post("/tracked/save-cbz", dependencies=[require_permission("sources.import")],
             response_model=List[TrackedSeries],
             summary="Whether a tracked comic series' new chapters are saved as CBZ files",
             responses=_ERR)
def post_tracked_save(payload: SourceTrackedSaveRequest, request: Request):
    return tracking.set_tracked_save(payload.source, payload.series_id, payload.save_cbz,
                                     principal=request.state.principal)


@router.post("/check-now", dependencies=[require_permission("sources.import")], response_model=SourcesJobStarted,
             summary="Job: check every tracked series for new chapters now (409 if one is running)",
             responses={**_ERR, 409: {"model": ErrorResponse}})
def post_check_now(request: Request):
    return tracking.start_check_now(local=is_local_request(request))


@router.post("/notifications/{notification_id}/dismiss", dependencies=[require_permission("sources.import")], response_model=SourceNotification,
             summary="Dismiss one new-chapter notification", responses=_ERR)
def post_dismiss(notification_id: int = Path(ge=1)):
    return svc.dismiss_notification(notification_id)


@router.post("/profiles/{domain}/{kind}/rollback", dependencies=[require_permission("admin.settings")], response_model=List[SourceProfileVersion],
             summary="Make an earlier saved profile version active again", responses=_ERR)
def post_profile_rollback(payload: SourceProfileRollbackRequest,
                          domain: str = Path(min_length=1, max_length=200),
                          kind: str = Path(min_length=1, max_length=40)):
    return svc.rollback_profile(domain, kind, payload.version)


@router.post("/{name}/enabled", dependencies=[require_permission("admin.settings")], response_model=SourceSummary,
             summary="Switch one source on or off", responses=_ERR)
def post_enabled(payload: SourceToggle, name: str = _NAME):
    return svc.set_source_enabled(name, payload.enabled)


@router.post("/{name}/adult", dependencies=[require_permission("admin.settings")], response_model=SourceSummary,
             summary="Adult-flagged works toggle (only sources that support it)",
             responses={**_ERR, 400: {"model": ErrorResponse}})
def post_adult(payload: SourceToggle, name: str = _NAME):
    return svc.set_adult_enabled(name, payload.enabled)


@router.post("/{name}/pace", dependencies=[local_only()], response_model=SourceSummary,
             summary="How careful requests to one source are (fast only where the source allows it)",
             responses=_ERR)
def post_pace(payload: SourcePaceRequest, name: str = _NAME):
    return svc.set_source_pace(name, payload.pace)


@router.post("/{name}/extension-only", dependencies=[require_permission("admin.settings")],
             response_model=SourceExtensionOnly,
             summary="Mark or unmark a source as working only through the browser extension",
             responses=_ERR)
def post_extension_only(payload: SourceExtensionOnlyRequest, request: Request, name: str = _NAME):
    return svc.set_extension_only(name, payload.extension_only, payload.note,
                                  principal=request.state.principal)


@router.post("/{name}/health/reset", dependencies=[require_permission("admin.settings")], response_model=SourceHealth,
             summary="Clear a source's backoff window (an explicit user action)", responses=_ERR)
def post_health_reset(name: str = _NAME):
    return svc.reset_health(name)


@router.get("/{name}/attempts", dependencies=[require_permission("admin.settings")], response_model=List[SourceAttempt],
            summary="Recent access attempts (URLs without query strings, text scrubbed)",
            responses={404: {"model": ErrorResponse}})
def list_attempts(name: str = _NAME, limit: int = Query(20, ge=1, le=100)):
    return svc.list_attempts(name, limit)
