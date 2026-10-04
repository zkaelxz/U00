"""api/schemas/sources.py -- Sources, Discover and tracked-series shapes.
"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

__all__ = [
    "KnownTitle",
    "KnownTitleList",
    "KnownTitleCreate",
    "KnownTitleDelete",
    "KnownTitleDeleted",
    "KnownTitleSeedResult",
    "DiscoverPlatforms",
    "DiscoverSearchLinks",
    "SourceSupports",
    "SourceSummary",
    "SourceHealth",
    "SourceTierResult",
    "SourceDetail",
    "SourceAttempt",
    "SourceCacheStats",
    "SourcesSettings",
    "SourceProfileVersion",
    "SourceProfileDomain",
    "TrackedSeries",
    "SourceNotification",
    "SourceToggle",
    "SourcesSettingsUpdate",
    "SourceCacheClearRequest",
    "SourceProfileRollbackRequest",
    "SourceTrackRequest",
    "DiscoverTranslateQueryRequest",
    "DiscoverTranslateQueryResult",
    "DiscoverBaihehubSearchRequest",
    "DiscoverBaihehubHit",
    "DiscoverBaihehubResult",
    "DiscoverImportSuggestionRequest",
    "DiscoverImportSuggestion",
    "DiscoverBulkExtractRequest",
    "DiscoverNavigationHelpRequest",
    "DiscoverJobStarted",
    "DiscoverJobResult",
    "DiscoverBulkEntry",
    "DiscoverBulkCommitRequest",
    "DiscoverBulkCommitResult",
    "SourcesSearchRequest",
    "SourcesSeriesRequest",
    "SourcesJobStarted",
    "SourcesJobResult",
    "SourcesChapterImportRequest",
    "SourcesChapterSaveRequest",
    "SourcesUrlPreviewRequest",
    "SourcesUrlImportRequest",
    "SourceSigninOpenRequest",
    "SourceSigninForgetRequest",
    "SourceSigninForgetResult",
    "SourceTierTestRequest",
    "SourceTrackedDramaRequest",
    "SourceTrackedSaveRequest",
    "SourcesProxyRequest",
    "SourceDomainList",
    "SourceDomainsUpdate",
    "SourceDomainProposal",
    "SourceDomainProposalAction",
    "SourceDomainProposalDismissed",
    "LncrawlStatus",
    "LncrawlImportRequest",
]


# --- Discover catalog -------------------------------------------------------
class KnownTitle(BaseModel):
    id: int
    title_original: Optional[str] = None
    title_en: Optional[str] = None
    author: Optional[str] = None
    tags: Optional[str] = None
    summary_en: Optional[str] = None
    summary_original: Optional[str] = None
    source_name: Optional[str] = None
    source_url: Optional[str] = None
    language: Optional[str] = None
    media_type: Optional[str] = None
    created_at: Optional[str] = None


class KnownTitleList(BaseModel):
    titles: List[KnownTitle]
    total: int


class KnownTitleCreate(BaseModel):
    """Whitelisted manual-add fields; unknown fields are 422."""
    model_config = ConfigDict(extra="forbid")
    title_original: str = Field(max_length=300)
    title_en: str = Field("", max_length=300)
    author: str = Field("", max_length=300)
    tags: str = Field("", max_length=500)
    summary_en: str = Field("", max_length=5000)
    summary_original: str = Field("", max_length=5000)
    source_name: str = Field("", max_length=100)
    source_url: str = Field("", max_length=2000)
    language: str = Field(max_length=10)
    media_type: str = Field(max_length=40)


class KnownTitleDelete(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: bool = False


class KnownTitleDeleted(BaseModel):
    deleted: bool
    id: int


class KnownTitleSeedResult(BaseModel):
    added: int
    total: int


class DiscoverPlatforms(BaseModel):
    platforms: List[Dict[str, Any]]


class DiscoverSearchLinks(BaseModel):
    links: List[Dict[str, Any]]


# ---------------------------------------------------------------------------
# Sources registry and status (S-1). Read-only; S-2 adds
# the write request models below. No proxy URL, path or query string is ever
# part of these shapes.
# ---------------------------------------------------------------------------
class SourceSupports(BaseModel):
    search: bool
    get_series: bool
    get_chapters: bool
    get_pages: bool
    download_page: bool
    get_chapter_text: bool
    get_audio_url: bool
    login: bool


class SourceSummary(BaseModel):
    name: str
    display_name: str
    content_types: List[str]
    languages: List[str]
    supports: SourceSupports
    import_supported: bool
    auth_supported: bool
    supports_adult_toggle: bool
    enabled: bool
    adult_enabled: bool
    health: str = Field(description="green, yellow or red.")
    has_saved_signin: bool


class SourceHealth(BaseModel):
    light: str
    consecutive_failures: int
    last_success: Optional[float] = None
    last_failure: Optional[float] = None
    last_error_type: Optional[str] = None
    last_error_category: Optional[str] = Field(
        default=None, description="blocked, site_down, page_missing, layout_changed, slow, "
                                  "needs_sign_in or other; null when there is no error.")
    last_error: Optional[str] = None
    last_latency: Optional[float] = None
    unavailable_until: Optional[float] = None
    retry_after: Optional[float] = None


class SourceTierResult(BaseModel):
    tested: bool
    ok: bool
    reason: Optional[str] = None
    detail: Optional[str] = None
    at: Optional[float] = None


class SourceDetail(SourceSummary):
    status: str
    technical_status: str
    access_method: Optional[str] = None
    content_access_status: str
    authentication_required: str
    purchase_required: str
    technical_protection: str
    automation_permission: str
    ai_ml_use: str
    tiers: Dict[str, SourceTierResult]
    technical: Dict[str, Any]
    terms: Dict[str, Any] = Field(description="Recorded findings, information only. "
                                  "Enforcement is off: never read this as permitted.")
    terms_enforced: bool
    health_detail: SourceHealth


class SourceAttempt(BaseModel):
    url: str = Field(description="scheme+host+path only.")
    created_at: Optional[float] = None
    tier: Optional[str] = None
    test_now: bool = False
    ok: Optional[bool] = None
    technical_status: Optional[str] = None
    capability_status: Optional[str] = None
    reasons: List[str] = []
    lines: List[str] = []
    handoff: Optional[Dict[str, Any]] = None


class SourceCacheStats(BaseModel):
    entries: int
    bytes: int


class SourcesSettings(BaseModel):
    pace_min_delay: float
    pace_max_delay: float
    max_concurrent: int
    max_retries: int
    session_break_min_requests: int
    session_break_max_requests: int
    session_break_min_delay: float
    session_break_max_delay: float
    cache_mode: str
    cache_max_mb: int = Field(0, description="Kept-cache size ceiling in MB; 0 = no limit.")
    check_interval_hours: int
    auto_queue_new_chapters: bool
    demo_source_enabled: bool
    extraction_diagnostics: bool
    proxy_configured: bool = Field(description="Whether a proxy is set. The URL is never returned.")
    cache_modes: List[str]
    cache: SourceCacheStats


class SourceProfileVersion(BaseModel):
    version: Optional[int] = None
    kind: Optional[str] = None
    status: Optional[str] = None
    origin: Optional[str] = None
    created_at: Optional[float] = None
    approved: bool = False
    failures: int = 0
    last_failure_reason: Optional[str] = None
    last_used: Optional[float] = None


class SourceProfileDomain(BaseModel):
    domain: str
    versions: List[SourceProfileVersion]


class TrackedSeries(BaseModel):
    source: str
    series_id: str
    title: str
    url: str
    drama_id: Optional[int] = None
    last_checked: Optional[float] = None
    last_check_error: Optional[str] = None
    # New chapters are also saved as CBZ files (comic sources).
    save_cbz: bool = False


class SourceNotification(BaseModel):
    id: int
    source: str
    series_id: str
    chapter_id: str
    title: Optional[str] = None
    created_at: float
    dismissed: bool


# Sources config writes (S-2).
class SourceToggle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


class SourcesSettingsUpdate(BaseModel):
    """Partial update. `extra=forbid`: http_proxy_url, page_server_enabled and
    any unknown key are rejected (422). Ranges are checked by the service;
    pace_min_delay also has a floor at the built-in default (service check)."""
    model_config = ConfigDict(extra="forbid")
    pace_min_delay: Optional[float] = None
    pace_max_delay: Optional[float] = None
    max_concurrent: Optional[int] = None
    max_retries: Optional[int] = None
    session_break_min_requests: Optional[int] = None
    session_break_max_requests: Optional[int] = None
    session_break_min_delay: Optional[float] = None
    session_break_max_delay: Optional[float] = None
    cache_mode: Optional[str] = Field(None, max_length=40)
    cache_max_mb: Optional[int] = None
    check_interval_hours: Optional[int] = None
    auto_queue_new_chapters: Optional[StrictBool] = None
    demo_source_enabled: Optional[StrictBool] = None
    extraction_diagnostics: Optional[StrictBool] = None


class SourceCacheClearRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = False


class SourceProfileRollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)


class SourceTrackRequest(BaseModel):
    """Track (`tracked=true`) or untrack one series. Fetches nothing."""
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1, max_length=60)
    series_id: str = Field(min_length=1, max_length=200)
    tracked: StrictBool = True
    title: str = Field("", max_length=300)
    url: str = Field("", max_length=1000)
    drama_id: Optional[int] = Field(None, ge=1)


# ---------------------------------------------------------------------------
# API batch 1: Discover network helpers (spec D-2) -- /api/discover/...
# ---------------------------------------------------------------------------
class DiscoverTranslateQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    q: str = Field(max_length=500)
    engine: Optional[str] = Field(None, max_length=40,
                                  description="None = Claude, the Discover default (checked as paid).")


class DiscoverTranslateQueryResult(BaseModel):
    query: str
    translated: str
    engine: str


class DiscoverBaihehubSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    q: str = Field(max_length=500)


class DiscoverBaihehubHit(BaseModel):
    title: str
    url: str
    snippet: str


class DiscoverBaihehubResult(BaseModel):
    results: List[DiscoverBaihehubHit]
    fallback_url: str


class DiscoverImportSuggestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(max_length=2000)
    engine: Optional[str] = Field(None, max_length=40)


class DiscoverImportSuggestion(BaseModel):
    """A suggestion only; nothing is written. Apply it with POST /api/discover/titles."""
    suggestion: Dict[str, str]
    found: bool
    needs_manual: bool
    message: str


class DiscoverBulkExtractRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    urls: List[str] = Field(min_length=1, max_length=10)
    source_label: str = Field("", max_length=100)
    engine: Optional[str] = Field(None, max_length=40)


class DiscoverNavigationHelpRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(max_length=2000)
    goal: str = Field(max_length=500)
    target_language: str = Field("English", max_length=20)
    engine: Optional[str] = Field(None, max_length=40)


class DiscoverJobStarted(BaseModel):
    job_id: str
    started: bool


class DiscoverJobResult(BaseModel):
    """`result` is the job's own result once set: bulk extract
    {entries, pages, source_label}; navigation help {labels, steps,
    needs_manual, message}. Nothing run yet in this process: status "idle",
    job_id "", progress 0, no result."""
    job_id: str
    status: Optional[str] = None
    progress: float
    message: str
    result: Optional[Dict[str, Any]] = None


class DiscoverBulkEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(max_length=300)
    author: str = Field("", max_length=300)
    tags: str = Field("", max_length=500)
    source_url: str = Field("", max_length=2000)
    has_audio_drama: StrictBool = False
    language: str = Field("zh", max_length=10)
    # From the bulk-extract result: the server stores its own full URL for
    # it and ignores source_url (which the result shows without a query).
    entry_id: Optional[str] = Field(None, max_length=40)


class DiscoverBulkCommitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entries: List[DiscoverBulkEntry] = Field(min_length=1, max_length=500)
    source_label: str = Field("", max_length=100)


class DiscoverBulkCommitResult(BaseModel):
    added: int
    skipped: int
    ids: List[int]


# ---------------------------------------------------------------------------
# API batch 1: Sources search / series (spec S-3) -- /api/sources/...
# ---------------------------------------------------------------------------
class SourcesSearchRequest(BaseModel):
    """Names and text only, never URLs (adapters build their own)."""
    model_config = ConfigDict(extra="forbid")
    query: str = Field(max_length=200)
    sources: Optional[List[str]] = Field(None, max_length=100)


class SourcesSeriesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    series_id: str = Field(max_length=200)


class SourcesJobStarted(BaseModel):
    job_id: str


class SourcesJobResult(BaseModel):
    """`result` (only once done): search {kind, query, cancelled, results,
    errors, per_source_counts} or series {kind, source, series_id, info,
    chapters}. URLs are scheme+host+path only; text is scrubbed. Series
    jobs also carry `source` and `series_id` while queued/running, so a
    page can tell which series the per-source run is for. A job that has not
    run in this process answers status "idle" (job_id "", progress 0)."""
    job_id: str
    status: Optional[str] = None
    progress: Optional[float] = None
    message: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    source: Optional[str] = None
    series_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Sources S-4 chapter import (services/sources_import_service.py). Results
# are read with GET /api/sources/jobs/{job_id}/result (SourcesJobResult).
# ---------------------------------------------------------------------------
class SourcesChapterImportRequest(BaseModel):
    """Chapter ids only: never chapter objects or URLs (the job re-fetches
    the series' chapter list and keeps these ids). The drama must exist."""
    model_config = ConfigDict(extra="forbid")
    series_id: str = Field(min_length=1, max_length=200)
    chapter_ids: List[StrictStr] = Field(min_length=1, max_length=200)
    drama_id: int = Field(ge=1)


class SourcesChapterSaveRequest(BaseModel):
    """Chapter ids of one comic series to save as CBZ files on this PC
    (services/sources_save_service.py). Ids only, like an import."""
    model_config = ConfigDict(extra="forbid")
    series_id: str = Field(min_length=1, max_length=200)
    chapter_ids: List[StrictStr] = Field(min_length=1, max_length=200)


# ---------------------------------------------------------------------------
# Sources S-5 paste-a-URL preview and novel import
# (services/sources_url_service.py, services/sources_import_service.py).
# Results are read with GET /api/sources/jobs/{job_id}/result.
# ---------------------------------------------------------------------------
class SourcesUrlPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: StrictStr = Field(min_length=1, max_length=2000)


class SourcesUrlImportRequest(BaseModel):
    """Novel text only (thin slice). The drama must be a novel drama."""
    model_config = ConfigDict(extra="forbid")
    url: StrictStr = Field(min_length=1, max_length=2000)
    drama_id: int = Field(ge=1)


# ---------------------------------------------------------------------------
# Sources S-6 sign-in, SO17 tier tests, S-7 check-now, tracked-series drama
# link and the SO18 proxy (services/sources_signin_service.py,
# services/sources_tracking_service.py, services/sources_registry_service.py).
# Jobs are read with GET /api/sources/jobs/{job_id}/result.
# ---------------------------------------------------------------------------
class SourceSigninOpenRequest(BaseModel):
    """`url`: a page on the source's own site ("" = its login page)."""
    model_config = ConfigDict(extra="forbid")
    url: StrictStr = Field("", max_length=2000)


class SourceSigninForgetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = False


class SourceSigninForgetResult(BaseModel):
    source: str
    forgotten: bool
    has_saved_signin: bool


class SourceTierTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tier: Literal["static", "browser", "signed_in"]
    url: StrictStr = Field(min_length=1, max_length=2000)


class SourceTrackedDramaRequest(BaseModel):
    """Which drama a tracked series auto-imports into (null = none)."""
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1, max_length=60)
    series_id: str = Field(min_length=1, max_length=200)
    drama_id: Optional[int] = Field(None, ge=1)


class SourceTrackedSaveRequest(BaseModel):
    """Whether a tracked comic series' new chapters are saved as CBZ files."""
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1, max_length=60)
    series_id: str = Field(min_length=1, max_length=200)
    save_cbz: StrictBool


class SourcesProxyRequest(BaseModel):
    """"" clears it. Never returned: settings carry `proxy_configured` only."""
    model_config = ConfigDict(extra="forbid")
    url: StrictStr = Field("", max_length=500)


# Domain lists of sources that move between domains (PC-only routes). host or
# host:port only: never a scheme, path or query.
class SourceDomainList(BaseModel):
    source: str
    display_name: str
    domains: List[str] = Field(description="host or host:port (443 implicit), tried in this order (https).")
    default_domains: List[str] = Field(description="The adapter's own list.")
    customized: bool = Field(description="True when the owner's saved list is in use.")
    last_good: Optional[str] = Field(
        default=None, description="The host that last answered (tried first); null if none.")
    pending_proposals: int


class SourceDomainsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    domains: List[StrictStr] = Field(min_length=1, max_length=10,
                                     description="host or host:port, e.g. example.com, in order.")


class SourceDomainProposal(BaseModel):
    source: str
    display_name: str
    host: str
    found_at: float


class SourceDomainProposalAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: StrictStr = Field(min_length=1, max_length=60)
    host: StrictStr = Field(min_length=1, max_length=260)


class SourceDomainProposalDismissed(BaseModel):
    dismissed: bool


# --- Import with lightnovel-crawler (external program) ----------------------
class LncrawlStatus(BaseModel):
    """Booleans only: never the program's path."""
    installed: bool
    path_configured: bool


class LncrawlImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: StrictStr = Field(..., min_length=1, max_length=2000)
    chapters: Literal["all", "first", "last"] = "all"
    count: Optional[StrictInt] = Field(None, ge=1, le=5000)
    mode: Literal["append", "replace"] = "replace"
