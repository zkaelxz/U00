"""
api/backup_schemas.py -- request/response models for the automatic backup
routes (api/routers/backup_routes.py, roadmap Step 43 as redefined
2026-09-29). The only path on any model is `folder`, the backup folder the
owner typed themselves (every route here is local_only). Backup copies are
named by file name only; a name sent back is matched against the backup
folder's own listing, never used as a path.
"""

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

BackupFrequency = Literal["daily", "weekly", "monthly"]
SnapshotKind = Literal["db-only", "full"]


class SnapshotCopy(BaseModel):
    name: str
    created_at: Optional[str] = None
    size: int
    kind: Optional[SnapshotKind] = None
    drama_count: Optional[int] = None
    readable: bool
    kept_as: Optional[Literal["daily", "weekly"]] = Field(
        None, description="Which rotation slot keeps this copy (the 2 newest are daily, then "
                          "the first copy of each of the 2 most recent older weeks).")
    managed: bool = Field(True, description="Made by this library (or, in its own default "
                                            "folder, before copies were tagged). Only managed "
                                            "copies are rotated or deleted by 'delete all'.")
    sequence: Optional[int] = Field(None, description="This library's copy number; the highest "
                                                      "is the newest.")


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
    copies: List[SnapshotCopy] = Field(default_factory=list, description="Newest first.")


class AutoBackupSettingsUpdate(BaseModel):
    """Fields left out are unchanged. folder "" = the default folder."""
    model_config = ConfigDict(extra="forbid")
    enabled: Optional[StrictBool] = None
    frequency: Optional[BackupFrequency] = None
    include_media: Optional[StrictBool] = None
    folder: Optional[str] = Field(None, max_length=1024)


class BackupNowRequest(BaseModel):
    """include_media left out = the setting. replace is accepted from older
    clients and ignored: each backup adds a new copy and old copies rotate
    out."""
    model_config = ConfigDict(extra="forbid")
    replace: StrictBool = False
    include_media: Optional[StrictBool] = None


class BackupJobStarted(BaseModel):
    job_id: str


class SnapshotInfo(BaseModel):
    exists: bool
    readable: Optional[bool] = None
    choose_copy: Optional[bool] = Field(None, description="true = the newest copy can't be told "
                                                          "for sure; a restore must name one.")
    default_copy: Optional[str] = Field(None, description="The copy a restore uses when none is "
                                                          "named.")
    created_at: Optional[str] = None
    kind: Optional[SnapshotKind] = None
    size: Optional[int] = None
    app_version: Optional[str] = None
    drama_count: Optional[int] = None
    copies: List[SnapshotCopy] = Field(default_factory=list, description="Newest first.")


class SnapshotDrama(BaseModel):
    id: int
    title: str
    media_type: str
    line_count: int
    exists_now: bool = Field(description="The id is in use now, so a restore makes a new "
                                         "drama titled \"... (restored <date>)\".")


class SnapshotDramaList(BaseModel):
    name: str
    created_at: Optional[str] = None
    kind: SnapshotKind
    dramas: List[SnapshotDrama]


class RestoreDramaRequest(BaseModel):
    """drama_id is the drama's id inside the copy. Needs confirm=true
    and confirm_text "RESTORE"."""
    model_config = ConfigDict(extra="forbid")
    drama_id: StrictInt = Field(..., ge=1, le=2**31 - 1)
    confirm: StrictBool = False
    confirm_text: str = Field("", max_length=32)
    snapshot: Optional[str] = Field(None, min_length=1, max_length=64,
                                    description="A copy's name from the copies list; left "
                                                "out = default_copy (409 choose_copy when "
                                                "there is none).")


class RestoreDramaDone(BaseModel):
    drama_id: int
    restored_as_new: bool
    title: str
    media_restored: bool
    snapshot: str = Field(description="The name of the copy the drama came from.")
    snapshot_kind: SnapshotKind
    series: Literal["none", "linked", "recreated", "dropped_private"] = Field(
        description="linked: back in its series; recreated: its series was gone and came back "
                    "from the snapshot; dropped_private: its series has since been made private "
                    "by someone else, so the drama came back with no series.")
    counts: Dict[str, int]
    skipped_tables: List[str]


class DeleteSnapshotRequest(BaseModel):
    """Needs confirm=true and confirm_text "DELETE", and exactly one of
    snapshot (the copy to delete) or all=true (every managed copy). An
    unmanaged copy is deleted only with include_unmanaged=true."""
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = False
    confirm_text: str = Field("", max_length=32)
    snapshot: Optional[str] = Field(None, min_length=1, max_length=64,
                                    description="A copy's name from the copies list.")
    all: StrictBool = Field(False, description="true = delete every managed copy (no "
                                               "snapshot).")
    include_unmanaged: StrictBool = Field(False, description="true = also copies this library "
                                                             "doesn't manage.")


class DeleteSnapshotDone(BaseModel):
    deleted: bool
    count: int = 0
    kept_unmanaged: int = Field(0, description="Unmanaged copies left in place by 'delete all'.")


# -- import from a backup file ------------------------------------------------

class BackupFileDrama(BaseModel):
    id: int = Field(description="The drama's id inside the file.")
    title: str
    media_type: str
    line_count: int
    has_media: bool


class BackupFileDramaList(BaseModel):
    kind: Literal["zip", "database"]
    media_available: bool
    schema_differs: bool = Field(description="The file has columns this version doesn't know; "
                                             "they are ignored.")
    dramas: List[BackupFileDrama]


class ImportedDrama(BaseModel):
    source_id: int
    drama_id: int
    title: str
    media_imported: bool


class ImportDramasDone(BaseModel):
    imported: List[ImportedDrama]
    series_created: int
    media_imported: int
    counts: Dict[str, int]
