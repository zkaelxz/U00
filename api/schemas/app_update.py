"""api/schemas/app_update.py -- App and source updates, shutdown, ports and usage re-costing.
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool

__all__ = [
    "UsageRecostModelRow",
    "UsageRecostPreview",
    "UsageRecostApplyRequest",
    "UsageRecostResult",
    "ShutdownResponse",
    "UpdateStatus",
    "UpdateSettingsRequest",
    "UpdateInstallRequest",
    "UpdateInstallResponse",
    "SourceUpdateApplyResponse",
    "SourceUpdateStatus",
    "AppUpdateApplyResponse",
    "PortEntry",
    "PortsOverview",
]


# Clean stop for the installed app (POST /api/system/shutdown).
class ShutdownResponse(BaseModel):
    status: str = Field(description="`stopping`: jobs were asked to stop and the server exits shortly.")
    cancelled_jobs: int = Field(description="How many running or queued jobs were asked to stop.")


# App updates from the public GitHub Releases (api/routers/update_routes.py).
# Names, numbers and booleans only: never a URL or a filesystem path.
class UpdateStatus(BaseModel):
    current: Optional[str] = Field(None, description="Installed version; null for a source checkout.")
    installed: bool
    latest: Optional[str] = None
    update_available: bool
    notes: str = Field("", description="Release notes as plain text, links removed, truncated.")
    installer_name: Optional[str] = None
    size: Optional[int] = None
    checked_at: Optional[float] = None
    check_error: Optional[str] = None
    release_lookup: Literal["unchecked", "found", "no_installer_release", "not_found"] = Field(
        description="`not_found`: GitHub answered 404 (repository missing, renamed or private).")
    download: Literal["idle", "downloading", "verified", "failed"]
    downloaded_bytes: int
    download_error: Optional[str] = None
    verified: bool = Field(description="The downloaded installer matched the release's SHA-256.")
    verified_version: Optional[str] = Field(None, description="The version Install would start.")
    verified_name: Optional[str] = None
    can_install: bool
    signature_check: bool = Field(description="This copy has a release public key, so an update is "
                                              "accepted only with a valid release signature.")
    signature_verified: bool = Field(description="The verified download's release signature was checked.")
    auto_check: bool
    custom_source: bool = Field(description="BAIHE_UPDATE_REPO names another repository than the default.")
    app_update: Literal["none", "available", "needs_full_installer"] = Field(
        "none", description="Whether the newer release can update the app layer alone "
                            "(`available`: built against this install's Python, wheels and Caddy).")
    app_update_here: bool = Field(False, description="This server can switch its own app folder: "
                                                     "Windows, started by the Start-menu launcher.")
    app_size: Optional[int] = None
    app_download: Literal["idle", "downloading", "verified", "failed"] = "idle"
    app_downloaded_bytes: int = 0
    app_download_error: Optional[str] = None
    app_verified: bool = False
    app_verified_version: Optional[str] = None
    can_apply_app: bool = False
    app_last_result: Optional[str] = Field(None, description="The last app-only update's outcome, "
                                                             "in plain words.")
    app_last_result_at: Optional[float] = None


class UpdateSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    auto_check: StrictBool


class UpdateInstallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool


class UpdateInstallResponse(BaseModel):
    launched: bool
    installer_name: str
    version: str


class AppUpdateApplyResponse(BaseModel):
    started: bool = Field(description="The swap helper took over: the app stops, switches and "
                                      "starts again (or goes back to this version).")
    version: str


# --- Diagnostics ports panel ---------------------------------------------------
class PortEntry(BaseModel):
    """One port Baihe uses: numbers, booleans and fixed text only."""
    key: Literal["api", "household", "extension", "https"]
    label: str
    port: Optional[int] = None
    active: bool
    how_to_change: str


class PortsOverview(BaseModel):
    """GET /api/diagnostics/ports (PC only)."""
    ports: List[PortEntry]


class UsageRecostModelRow(BaseModel):
    model: str
    rows: int
    stored_usd: float
    recomputed_usd: float


class UsageRecostPreview(BaseModel):
    """GET /api/settings/usage-recost: what a re-cost would change. Numbers
    only; nothing is written."""
    rows: int
    models: List[UsageRecostModelRow]
    stored_usd: float
    recomputed_usd: float
    difference_usd: float
    month_stored_usd: float
    month_recomputed_usd: float
    # Rows a re-cost already replaced, which Undo can put back.
    recosted_rows: int
    # Digest of the exact (row, new cost) set; apply refuses a different set
    # even when it has the same number of rows.
    fingerprint: str


class UsageRecostApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = False
    # What the user saw in the preview; a different count or set now means 409.
    previewed: int = Field(ge=0)
    fingerprint: str = Field(min_length=1, max_length=64)


class UsageRecostResult(BaseModel):
    """POST apply or undo: rows written and this month's logged spend after."""
    rows: int
    month_spend_usd: float
    # Rows holding a replaced cost after this call, so a client need not accumulate.
    recosted_rows: int
    # Apply only: the previewed count the client sent and rows actually changed.
    previewed: Optional[int] = None
    changed: Optional[int] = None


# Update and restart for a git working copy (api/routers/source_update_routes.py).
# Short commit ids, counts and plain-words reasons only.
class SourceUpdateStatus(BaseModel):
    available: bool = Field(description="This copy is a git folder, so the card applies.")
    current: Optional[str] = Field(None, description="Short commit id this copy is on.")
    checked: bool
    behind: Optional[int] = Field(None, description="Commits waiting; null until checked.")
    latest: Optional[str] = None
    changes: List[str] = Field(default_factory=list, description="Subjects of the newest waiting commits.")
    reason: Optional[str] = Field(None, description="Why an update can't run, in plain words.")
    can_apply: bool
    applying: bool
    last_result: Optional[str] = Field(None, description="The last update's outcome, in plain words.")
    last_result_at: Optional[float] = None


class SourceUpdateApplyResponse(BaseModel):
    started: bool
    from_commit: str
