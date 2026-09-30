"""
api/backup_schemas.py -- request/response models for the automatic backup
routes (api/routers/backup_routes.py, roadmap Step 43 as redefined
2026-09-29). Kept out of api/schemas.py so this slice could be built
alongside other branches editing that file; the shared ErrorResponse still
lives there. The only path on any model is `folder`, the backup folder the
owner typed themselves (every route here is local_only).
"""

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

BackupFrequency = Literal["daily", "weekly", "monthly"]
SnapshotKind = Literal["db-only", "full"]


class AutoBackupSettings(BaseModel):
    enabled: bool
    frequency: BackupFrequency
    include_media: bool
    folder: str = Field(description='"" = the library\'s own backups folder.')
    frequencies: List[str]
    last_run_at: Optional[str] = None
    last_attempt_at: Optional[str] = None
    last_error: Optional[str] = None
    next_run_at: Optional[str] = None
    running: bool


class AutoBackupSettingsUpdate(BaseModel):
    """Fields left out are unchanged. folder "" = the default folder."""
    model_config = ConfigDict(extra="forbid")
    enabled: Optional[StrictBool] = None
    frequency: Optional[BackupFrequency] = None
    include_media: Optional[StrictBool] = None
    folder: Optional[str] = Field(None, max_length=1024)


class BackupNowRequest(BaseModel):
    """replace=true is needed when a snapshot exists (it is replaced).
    include_media left out = the setting."""
    model_config = ConfigDict(extra="forbid")
    replace: StrictBool = False
    include_media: Optional[StrictBool] = None


class BackupJobStarted(BaseModel):
    job_id: str


class SnapshotInfo(BaseModel):
    exists: bool
    readable: Optional[bool] = None
    created_at: Optional[str] = None
    kind: Optional[SnapshotKind] = None
    size: Optional[int] = None
    app_version: Optional[str] = None
    drama_count: Optional[int] = None


class SnapshotDrama(BaseModel):
    id: int
    title: str
    media_type: str
    line_count: int
    exists_now: bool = Field(description="The id is in use now, so a restore makes a new "
                                         "drama titled \"... (restored <date>)\".")


class SnapshotDramaList(BaseModel):
    created_at: Optional[str] = None
    kind: SnapshotKind
    dramas: List[SnapshotDrama]


class RestoreDramaRequest(BaseModel):
    """drama_id is the drama's id inside the snapshot. Needs confirm=true
    and confirm_text "RESTORE"."""
    model_config = ConfigDict(extra="forbid")
    drama_id: StrictInt = Field(..., ge=1, le=2**31 - 1)
    confirm: StrictBool = False
    confirm_text: str = Field("", max_length=32)


class RestoreDramaDone(BaseModel):
    drama_id: int
    restored_as_new: bool
    title: str
    media_restored: bool
    snapshot_kind: SnapshotKind
    series: Literal["none", "linked", "recreated", "dropped_private"] = Field(
        description="linked: back in its series; recreated: its series was gone and came back "
                    "from the snapshot; dropped_private: its series has since been made private "
                    "by someone else, so the drama came back with no series.")
    counts: Dict[str, int]
    skipped_tables: List[str]


class DeleteSnapshotRequest(BaseModel):
    """Needs confirm=true and confirm_text "DELETE"."""
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = False
    confirm_text: str = Field("", max_length=32)


class DeleteSnapshotDone(BaseModel):
    deleted: bool
