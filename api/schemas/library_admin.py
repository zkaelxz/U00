"""api/schemas/library_admin.py -- Library admin shapes: bulk status, tags, series,
delete and translate, export, backup, artifacts, restore and storage cleanup
(services/library_admin_service.py)."""

from enum import Enum
from typing import TYPE_CHECKING, Annotated, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from services.library_admin_service import MAX_BULK_IDS as _MAX_BULK_IDS, STATUSES as _LIBRARY_STATUSES
import db as _db
import storage as _storage

__all__ = [
    "LibraryDramaIds",
    "LibraryStatus",
    "LibraryListTag",
    "LibraryStoragePreset",
    "LibraryArtifactKind",
    "LibraryBulkStatusRequest",
    "LibraryBulkTagRequest",
    "LibraryBulkSeriesRequest",
    "LibraryBulkDeleteRequest",
    "LibraryBulkTranslateRequest",
    "LibraryExportRequest",
    "LibraryBackupRequest",
    "LibraryUserBackupRequest",
    "LibraryStorageCleanRequest",
    "LibraryBulkItem",
    "LibraryBulkResult",
    "LibraryBulkDeleteResult",
    "LibraryBulkTranslateSkip",
    "LibraryBulkTranslateStarted",
    "LibraryExportStarted",
    "LibraryJobStarted",
    "LibraryArtifactInfo",
    "LibraryRestoreDone",
    "LibraryStorageCategory",
    "LibraryStorageDrama",
    "LibraryStorageScan",
    "TempCleanResult",
    "LibraryStorageCleanResult",
]


LibraryDramaIds = Annotated[List[Annotated[StrictInt, Field(ge=1, le=2**31 - 1)]],
                            Field(min_length=1, max_length=_MAX_BULK_IDS)]


# The choices come from a runtime tuple, which a type checker can't read as a Literal.
if TYPE_CHECKING:
    LibraryStatus = str
    LibraryListTag = str
    LibraryStoragePreset = str
else:
    LibraryStatus = Literal[_LIBRARY_STATUSES]
    LibraryListTag = Literal[tuple(_db.ORGANIZATIONAL_TAGS)]
    LibraryStoragePreset = Literal[tuple(_storage.STORAGE_QUALITY_PRESETS)]


class LibraryArtifactKind(str, Enum):
    backup = "backup"
    export = "export"
    database = "database"
    user_backup = "user_backup"


class LibraryBulkStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    drama_ids: LibraryDramaIds
    status: LibraryStatus


class LibraryBulkTagRequest(BaseModel):
    """Adds (present=true) or removes one organizational list tag."""
    model_config = ConfigDict(extra="forbid")
    drama_ids: LibraryDramaIds
    tag: LibraryListTag
    present: StrictBool


class LibraryBulkSeriesRequest(BaseModel):
    """Puts every picked title in one series: `series_id` (0 takes them out)
    or `new_series_name`, exactly one."""
    model_config = ConfigDict(extra="forbid")
    drama_ids: LibraryDramaIds
    series_id: Optional[StrictInt] = Field(default=None, ge=0, le=2**31 - 1)
    new_series_name: Optional[str] = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def _exactly_one(self):
        if (self.series_id is None) == (self.new_series_name is None):
            raise ValueError("Pass series_id or new_series_name.")
        return self


class LibraryBulkDeleteRequest(BaseModel):
    """Needs confirm=true and confirm_text "DELETE"."""
    model_config = ConfigDict(extra="forbid")
    drama_ids: LibraryDramaIds
    confirm: StrictBool = False
    confirm_text: str = Field("", max_length=32)


class LibraryBulkTranslateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    drama_ids: LibraryDramaIds
    # Omitted: the Settings default English variant.
    default_locale: Optional[str] = Field(None, max_length=5)
    # Omitted: each title's saved choice (else genre notes on, she/her off).
    include_genre_notes: Optional[StrictBool] = None
    default_female_pronouns: Optional[StrictBool] = None


class LibraryExportRequest(BaseModel):
    """drama_ids omitted: every translated/dubbed/exported drama."""
    model_config = ConfigDict(extra="forbid")
    drama_ids: Optional[LibraryDramaIds] = None
    include_originals: StrictBool = Field(
        default=False, description="Also zip every title's original audio, video, novel text and "
        "page scans, not only those of titles pinned with \"Keep original files\".")


class LibraryBackupRequest(BaseModel):
    """database_only=true: the database snapshot alone (fast, small)."""
    model_config = ConfigDict(extra="forbid")
    database_only: StrictBool = False


class LibraryUserBackupRequest(BaseModel):
    """Backup of one person's dramas and series. user_id omitted or null:
    the items owned at the PC (no owner)."""
    model_config = ConfigDict(extra="forbid")
    user_id: Optional[StrictInt] = Field(None, ge=1)


class LibraryStorageCleanRequest(BaseModel):
    """Needs confirm=true and confirm_text "CLEAN"."""
    model_config = ConfigDict(extra="forbid")
    preset: LibraryStoragePreset
    confirm: StrictBool = False
    confirm_text: str = Field("", max_length=32)


class LibraryBulkItem(BaseModel):
    """One requested drama's outcome. error: not_found, job_running,
    delete_failed, not_translated, no_translated_lines, not_allowed, conflict or failed."""
    drama_id: int
    ok: bool
    error: Optional[str] = None
    message: Optional[str] = None
    warning: Optional[str] = None
    freed_bytes: Optional[int] = None


class LibraryBulkResult(BaseModel):
    results: List[LibraryBulkItem]
    updated: int


class LibraryBulkDeleteResult(BaseModel):
    results: List[LibraryBulkItem]
    deleted: int


class LibraryBulkTranslateSkip(BaseModel):
    drama_id: int
    reason: str


class LibraryBulkTranslateStarted(BaseModel):
    job_id: str
    queued: List[int]
    skipped: List[LibraryBulkTranslateSkip]


class LibraryExportStarted(BaseModel):
    job_id: str
    drama_ids: List[int]
    results: Optional[List[LibraryBulkItem]] = None


class LibraryJobStarted(BaseModel):
    job_id: str


class LibraryArtifactInfo(BaseModel):
    """The newest finished file of one kind. Never a path."""
    kind: LibraryArtifactKind
    name: str
    size: int


class LibraryRestoreDone(BaseModel):
    restored: bool
    sessions_revoked: int
    # True when the backup held no media and the current media was kept.
    media_kept: bool = False
    # The previous library stayed beside the restored one (something in it
    # could not be moved across): the owner must check before deleting it.
    previous_library_kept: bool = False
    # Accounts taken from the backup (only into a library that had none).
    accounts_restored: int = 0
    # Titles whose owner became the live account with the same email, or the
    # PC owner (only when the live accounts were kept).
    titles_reassigned: int = 0
    # False: the backup holds no readable account, so every title became the PC
    # owner's (the single-title restore's copy_has_accounts notice). Always True
    # when the backup's accounts were taken. No UI reads it yet.
    backup_has_accounts: bool = True
    # The previous library's other folders the backup lacks: they stay there.
    folders_left_behind: List[str] = []


class LibraryStorageCategory(BaseModel):
    key: str
    label: str
    note: str
    bytes: int
    selected: bool


class LibraryStorageDrama(BaseModel):
    drama_id: int
    total_bytes: int
    would_free_bytes: int
    job_running: bool


class LibraryStorageScan(BaseModel):
    """Dry run: nothing is removed."""
    preset: str
    categories_to_clean: List[str]
    total_bytes: int
    reclaimable_bytes: int
    would_free_bytes: int
    categories: List[LibraryStorageCategory]
    per_drama: List[LibraryStorageDrama]


class TempCleanResult(BaseModel):
    """Counts only: never a path."""
    removed: int
    freed_mb: float


class LibraryStorageCleanResult(BaseModel):
    preset: str
    freed_bytes: int
    results: List[LibraryBulkItem]
