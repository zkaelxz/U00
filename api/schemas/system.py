"""api/schemas/system.py -- System-level shapes: health, diagnostics overview, jobs,
settings and engine keys.
"""

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr

from api.schemas.common import ConfirmRequest, TimingPreferences, TimingPreferencesUpdate

__all__ = [
    "MonthSpendStatus",
    "MonthCounterResetResult",
    "HealthResponse",
    "MetaResponse",
    "DependencyStatus",
    "FileCompleteness",
    "GpuStatus",
    "GpuReading",
    "ModelEngineVersion",
    "RunningJob",
    "DiagnosticsOverview",
    "RemoteHealthState",
    "RemoteHealthCheck",
    "RemoteCertificateCheck",
    "RemoteDdnsCheck",
    "RemoteHealth",
    "RemoteIpCheckStatus",
    "RemoteIpCheckSetRequest",
    "RemoteIpCheckClearRequest",
    "RemoteIpCheckTestResult",
    "JobRecord",
    "JobListResponse",
    "JobQueuePosition",
    "JobQueueSummary",
    "SettingsPreferences",
    "SettingsChoices",
    "SettingsOverview",
    "SettingsUpdateRequest",
    "JobCancelResult",
    "JobDeleteResult",
    "JobForceStopResult",
    "JobsClearFinishedResult",
    "ArtifactInfo",
    "EngineKeySetRequest",
    "EngineKeyClearRequest",
    "EngineKeyResult",
    "EndpointUrlSetRequest",
    "EndpointUrlResult",
    "OcrBackendStatus",
]


class HealthResponse(BaseModel):
    status: str = Field(description="`ok` whenever the server can answer at all.")


class MetaResponse(BaseModel):
    app: str
    api_version: str
    environment: str = Field(description="`development` or `production`; empty on the "
                                         "household listener.")
    local: bool = Field(description="True when this request would pass a PC-only (local_only) "
                                    "route: the viewer is at the PC. A UI hint only; those "
                                    "routes still enforce it.")
    features: Dict[str, bool] = Field(default_factory=dict,
                                      description="Feature switches (`jellyfin`, `sources`, "
                                                  "`extension`): false when the area is switched "
                                                  "off, so the UI hides it.")


class DependencyStatus(BaseModel):
    installed: bool
    powers: str
    tier: str


class FileCompleteness(BaseModel):
    missing_top_level: List[str]
    all_present: bool


class GpuStatus(BaseModel):
    available: bool
    name: Optional[str] = None
    vram_used_gb: Optional[float] = None
    vram_total_gb: Optional[float] = None
    torch_cuda_version: Optional[str] = None
    message: Optional[str] = None


class GpuReading(BaseModel):
    found: bool
    name: Optional[str] = None
    vram_used_gb: Optional[float] = None
    vram_total_gb: Optional[float] = None


class ModelEngineVersion(BaseModel):
    name: str
    version: Optional[str] = None
    url: Optional[str] = None
    installed: bool
    package: Optional[str] = None
    help: Optional[str] = None


class RunningJob(BaseModel):
    job_id: str
    status: Optional[str] = None
    progress: Optional[float] = None
    message: str = ""
    error: Optional[str] = None
    description: Optional[str] = None
    gpu_touching: bool = False
    started_at: Optional[float] = None
    finished_at: Optional[float] = None


class DiagnosticsOverview(BaseModel):
    """A read-only snapshot of Diagnostics' routine view -- no admin action (install/upgrade/delete) is exposed
    here; those are PC-only routes under /api/diagnostics. Log lines and
    job messages/errors are redacted."""
    dependencies: dict[str, DependencyStatus]
    file_completeness: FileCompleteness
    library_writable: bool
    gpu: GpuStatus
    model_engine_versions: List[ModelEngineVersion]
    running_jobs: List[RunningJob]
    recent_log_lines: List[str]


RemoteHealthState = Literal["off", "unknown", "not_configured", "ok", "warn", "critical"]


class RemoteHealthCheck(BaseModel):
    state: RemoteHealthState
    message: str


class RemoteCertificateCheck(RemoteHealthCheck):
    days_left: Optional[int] = None


class RemoteDdnsCheck(RemoteHealthCheck):
    configured: bool


class RemoteHealth(BaseModel):
    """GET /api/diagnostics/remote-health: the last scheduled check of remote
    access. States, whole days, Unix times and fixed messages only: never the
    public name, an address, a URL or a path."""
    state: Literal["off", "unknown", "ok", "warn", "critical"]
    message: str
    checked_at: Optional[float] = None
    since: Optional[float] = None
    certificate: RemoteCertificateCheck
    ddns: RemoteDdnsCheck
    listener: RemoteHealthCheck


class RemoteIpCheckStatus(BaseModel):
    """Whether the public-address check is set; never the address."""
    configured: bool


class RemoteIpCheckSetRequest(BaseModel):
    """Write-only: `value` may carry a token, so it is never echoed back and
    validation errors never include it."""
    model_config = ConfigDict(extra="forbid")
    value: str = Field(..., repr=False)
    confirm: StrictBool = False


class RemoteIpCheckClearRequest(ConfirmRequest):
    pass


class RemoteIpCheckTestResult(BaseModel):
    """One check run now: a state and a fixed message, no address."""
    configured: bool
    state: RemoteHealthState
    message: str


class JobRecord(BaseModel):
    """One job's cross-process record (read from the
    job_records mirror) -- the last status this app
    knows about, from any process, not necessarily the current one (see
    job_records' own "no resume" limitation)."""
    job_id: str
    status: str
    progress: Optional[float] = None
    message: str = ""
    error: Optional[str] = None
    description: Optional[str] = None
    gpu_touching: bool = False
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    updated_at: float
    # A redacted, allowlisted projection of the job's result dict
    # (services/jobs_service.project_result) plus a normalised outcome
    # (ok | failed | cancelled | interrupted | partial | kept_existing), so a
    # "done" job that actually failed or was cancelled does not look like a
    # success.
    result: Optional[Dict[str, Any]] = None
    outcome: Optional[str] = None
    outcome_message: Optional[str] = None
    # Still queued/running on record, but no owner has heartbeated it for
    # 15 minutes (server clock): left behind by a process that died.
    stale: bool = False
    # Running here, but no progress update for a while (advisory; the state
    # is unchanged). Distinct from `stale`, which is about a dead owner.
    stalled: bool = False
    # Cancel was heard over a minute ago and the worker still runs: the
    # Force stop button's condition (job_force_stop.can_force_stop).
    can_force_stop: bool = False
    # The caller started this job or it runs on the caller's title (auth
    # off and the local owner: every job).
    # Server-computed from the caller's session; true only where the caller
    # may also cancel it (admins may cancel every job).
    owned_by_me: bool = False
    # The title a drama-scoped job runs on, and a fixed-vocabulary label for
    # what it does (services/jobs_service.JOB_KIND_BY_PREFIX), so clients
    # never parse job ids. Only set for jobs the caller may already see.
    drama_id: Optional[int] = None
    kind: Literal["transcribe", "translate", "align", "dub", "export", "review",
                  "import", "other"] = "other"
    # The page a job belongs to (services/jobs_service.job_page), so a job
    # with no title can still link somewhere. A page name only; None when
    # the id names no page.
    page: Optional[Literal["title", "sources", "discover", "live", "settings",
                           "diagnostics"]] = None


class JobListResponse(BaseModel):
    items: List[JobRecord]
    count: int


class SettingsPreferences(TimingPreferences):
    """Persisted PC-side preferences. Paths are paths only: a cookies file's contents are never
    read or returned. The four paths are returned only to the PC itself;
    any other caller gets "" there and only the *_configured booleans."""
    default_engine: str
    default_locale: str
    default_style_note: str
    scene_aware_batches: bool
    translate_reply_guard: str = "auto"
    episode_summary_engine: str
    monthly_cap_usd: Optional[float] = None
    max_upload_mb: int = 20480
    ollama_num_ctx_override: int
    keep_free_vram_gb: float = 0.0
    keep_free_ram_gb: float = 0.0
    whisper_model_path: str
    ocr_backend: str
    ocr_prefer_paddle_vl_manga: bool
    tesseract_cmd: str
    cookies_browser: Optional[str] = None
    cookies_file: str
    lncrawl_cmd: str = ""
    whisper_model_path_configured: bool = False
    tesseract_cmd_configured: bool = False
    cookies_file_configured: bool = False
    lncrawl_cmd_configured: bool = False


class OcrBackendStatus(BaseModel):
    installed: bool
    reason: Optional[str] = None
    package: Optional[str] = None


class SettingsChoices(BaseModel):
    engines: List[str]
    locales: List[str]
    summary_engines: List[str]
    ocr_backends: List[str]
    ocr_backend_status: Dict[str, OcrBackendStatus] = {}
    cookie_browsers: List[str]


class SettingsOverview(BaseModel):
    """Non-secret settings snapshot -- engine_keys
    reports only whether a key/endpoint is configured, never its value
    (keys are server-side only). endpoints carries the Ollama
    URL only when it has no userinfo,
    query or fragment (settings_service.validate_endpoint_url)."""
    engine_keys: dict[str, bool]
    gpu_limit_enabled: bool
    gpu_max_parallel: int = 1
    unload_ollama_before_transcribe: bool = True
    notify_on_completion: bool
    use_gpu: bool = False
    gemini_free_tier: bool = False
    bulk_auto_resume: bool = False
    offer_provider_models: bool = False
    # Developer Mode is on and the switch hasn't lapsed (settings_service.remote_uploads_on).
    remote_uploads: bool = False
    preferences: SettingsPreferences
    endpoints: Dict[str, Optional[str]]
    # BAIHE_MAX_UPLOAD_MB set: it wins over preferences.max_upload_mb.
    upload_max_mb_from_env: bool = False
    effective_upload_max_mb: int = 20480
    monthly_cap_env_usd: float = 0.0
    effective_monthly_cap_usd: float = 0.0
    # Full month vs what the cap counts since an active reset (UTC ISO time).
    month_spend_usd: float = 0.0
    month_spend_counted_usd: float = 0.0
    month_spend_reset_at: Optional[str] = None
    choices: SettingsChoices


class MonthSpendStatus(BaseModel):
    month_spend_usd: float
    month_spend_counted_usd: float
    month_spend_reset_at: Optional[str] = None


class MonthCounterResetResult(BaseModel):
    """POST reset or undo: month spend figures before and after. Numbers and
    a timestamp only."""
    before: MonthSpendStatus
    after: MonthSpendStatus


class SettingsUpdateRequest(TimingPreferencesUpdate):
    """Non-secret Settings writes (preferences). Unknown fields are rejected; keys and endpoint URLs
    are never accepted here (they have their own guarded routes).
    settings_service.set_settings re-validates every value. For
    monthly_cap_usd, null clears the saved cap (the .env value applies)."""
    model_config = ConfigDict(extra="forbid")
    gpu_limit_enabled: Optional[StrictBool] = None
    gpu_max_parallel: Optional[StrictInt] = None  # clamped to 1..4
    unload_ollama_before_transcribe: Optional[StrictBool] = None
    notify_on_completion: Optional[StrictBool] = None
    use_gpu: Optional[StrictBool] = None
    gemini_free_tier: Optional[StrictBool] = None
    bulk_auto_resume: Optional[StrictBool] = None
    offer_provider_models: Optional[StrictBool] = None
    remote_uploads: Optional[StrictBool] = None
    default_engine: Optional[StrictStr] = Field(None, max_length=40)
    default_locale: Optional[StrictStr] = Field(None, max_length=8)
    default_style_note: Optional[StrictStr] = Field(None, max_length=2000)
    scene_aware_batches: Optional[StrictBool] = None
    translate_reply_guard: Optional[StrictStr] = Field(None, max_length=40)
    episode_summary_engine: Optional[StrictStr] = Field(None, max_length=40)
    monthly_cap_usd: Optional[Union[StrictInt, StrictFloat]] = None
    max_upload_mb: Optional[StrictInt] = None
    ollama_num_ctx_override: Optional[StrictInt] = None
    keep_free_vram_gb: Optional[Union[StrictInt, StrictFloat]] = None
    keep_free_ram_gb: Optional[Union[StrictInt, StrictFloat]] = None
    whisper_model_path: Optional[StrictStr] = Field(None, max_length=1024)
    ocr_backend: Optional[StrictStr] = Field(None, max_length=40)
    ocr_prefer_paddle_vl_manga: Optional[StrictBool] = None
    tesseract_cmd: Optional[StrictStr] = Field(None, max_length=1024)
    cookies_browser: Optional[StrictStr] = Field(None, max_length=40)
    cookies_file: Optional[StrictStr] = Field(None, max_length=1024)
    lncrawl_cmd: Optional[StrictStr] = Field(None, max_length=1024)


class JobQueuePosition(BaseModel):
    job_id: str
    # 1 = the next to start.
    position: int


class JobQueueSummary(BaseModel):
    # Which line the positions are in: only GPU jobs wait.
    lane: Literal["gpu"]
    # Everyone's jobs, as counts only.
    running: int
    waiting: int
    # The caller's own waiting jobs (owned_by_me), next first.
    mine: List[JobQueuePosition]


class JobCancelResult(BaseModel):
    job_id: str
    cancel_requested: bool
    status: str


class JobForceStopResult(BaseModel):
    job_id: str
    force_stopped: bool
    status: str
    # The worker thread cannot be killed: true while it is still finishing,
    # in which case the job id cannot be started again until it ends.
    worker_still_running: bool


class JobDeleteResult(BaseModel):
    job_id: str
    deleted: bool


class JobsClearFinishedResult(BaseModel):
    deleted_count: int


class ArtifactInfo(BaseModel):
    name: str
    size: int
    kind: str


class EngineKeySetRequest(BaseModel):
    """Write-only engine key. `value` is a secret:
    it is never echoed back and validation errors never include it."""
    model_config = ConfigDict(extra="forbid")
    value: str = Field(..., repr=False)
    confirm: StrictBool = False


class EngineKeyClearRequest(ConfirmRequest):
    pass


class EngineKeyResult(BaseModel):
    engine: str
    configured: bool


class EndpointUrlSetRequest(BaseModel):
    """Ollama URL. An
    http(s) URL with no userinfo, query or fragment."""
    model_config = ConfigDict(extra="forbid")
    url: str = Field(..., max_length=300)
    confirm: StrictBool = False


class EndpointUrlResult(BaseModel):
    name: str
    url: Optional[str] = None
    configured: bool
