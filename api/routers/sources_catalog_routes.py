"""
api/routers/sources_catalog_routes.py -- the Sources registry and status
(Migration Slice 56). Prefix /api/sources. Deliberately a different file
name from source_routes.py, which is the Workspace Source stage
(/api/source/dramas/{id}/config).

Fixed-path routes (settings, profiles, tracked, notifications) are declared
before /{name} so they are never read as a source name.
"""

from typing import List

from fastapi import APIRouter, Path, Query

from api.schemas import (ErrorResponse, SourceAttempt, SourceDetail, SourceNotification,
                         SourceProfileDomain, SourcesSettings, SourceSummary, TrackedSeries)
from services import sources_registry_service as svc

router = APIRouter(prefix="/api/sources", tags=["sources"])

_NAME = Path(min_length=1, max_length=60)


@router.get("", response_model=List[SourceSummary], summary="List every source adapter")
def list_sources():
    return svc.list_sources()


@router.get("/settings", response_model=SourcesSettings,
            summary="Source pacing/cache settings (numbers and enums; proxy is a bool only)")
def get_settings():
    return svc.get_settings()


@router.get("/profiles", response_model=List[SourceProfileDomain],
            summary="Saved per-domain extraction profiles (version summaries only)")
def list_profiles():
    return svc.list_profiles()


@router.get("/tracked", response_model=List[TrackedSeries], summary="Tracked series")
def list_tracked():
    return svc.list_tracked()


@router.get("/notifications", response_model=List[SourceNotification],
            summary="New-chapter notifications")
def list_notifications(include_dismissed: bool = False):
    return svc.list_notifications(include_dismissed)


@router.get("/{name}", response_model=SourceDetail,
            summary="One source's capability record, health and recorded terms (information only)",
            responses={404: {"model": ErrorResponse}})
def get_source(name: str = _NAME):
    return svc.get_source(name)


@router.get("/{name}/attempts", response_model=List[SourceAttempt],
            summary="Recent access attempts (URLs without query strings, text scrubbed)",
            responses={404: {"model": ErrorResponse}})
def list_attempts(name: str = _NAME, limit: int = Query(20, ge=1, le=100)):
    return svc.list_attempts(name, limit)
