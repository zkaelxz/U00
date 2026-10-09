"""
api/disk_usage_schemas.py -- request/response models for the Disk usage
routes (api/routers/disk_usage_routes.py). Every `path` is RELATIVE to the
app's data folder, written with "/"; no model carries an absolute path or the
data folder's own path. The one absolute path in the API is the `destination`
the owner types for a move (a request field, never echoed back).
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr


class DiskUsageRegenerable(BaseModel):
    label: str
    note: str


class DiskUsageMovable(BaseModel):
    supported: bool
    reason: Optional[str] = Field(None, description="Why it can't be moved (when not supported).")
    what: Optional[Literal["backups"]] = None


class DiskUsageItem(BaseModel):
    name: str
    path: str = Field(description="Relative to the data folder, '/'-separated.")
    kind: Literal["file", "folder"]
    size_bytes: int
    file_count: int
    percent_of_parent: float
    modified_at: Optional[str] = None
    is_link: bool = False
    contains_link: bool = Field(False, description="A link or junction is somewhere inside; "
                                                   "clearing the folder whole is refused.")
    linked_bytes: Optional[int] = Field(
        None, description="Size of the linked folder(s) stored elsewhere that this item is or "
                          "holds, kept out of size_bytes. Null when none was measured; never "
                          "names where they are.")
    linked_files: Optional[int] = None
    linked_complete: Optional[bool] = Field(
        None, description="False when a limit cut the linked size short (a lower bound).")
    complete: bool = Field(True, description="False when the scan's limit cut this item's "
                                             "measurement short (size is a lower bound).")
    protected: bool
    protected_reason: Optional[str] = None
    regenerable: Optional[DiskUsageRegenerable] = Field(
        None, description="The app can rebuild this (storage.py's categories).")
    irreplaceable: bool = Field(False, description="Source media that can't be recreated; "
                                                   "clearing needs an extra confirm.")
    irreplaceable_note: Optional[str] = None
    movable: DiskUsageMovable


class DiskUsageTrashSummary(BaseModel):
    size_bytes: int
    item_count: int
    partial: bool = Field(False, description="The size is a lower bound (walk limit, or the "
                                             "Trash folder couldn't be read).")


class DiskUsageScan(BaseModel):
    path: str
    parent: Optional[str] = None
    total_bytes: int
    file_count: int
    linked_bytes: int = Field(0, description="Plus this much in linked folders stored "
                                             "elsewhere: not part of total_bytes.")
    linked_files: int = 0
    linked_complete: bool = True
    items: List[DiskUsageItem]
    partial: bool = Field(description="A walk limit (entries or time) was hit: sizes are "
                                      "lower bounds.")
    partial_reason: Optional[Literal["entries", "time", "items"]] = None
    not_shown: int = Field(0, description="Items left out because the folder holds more than "
                                          "the list limit or the walk limit was hit.")
    scanned_entries: int
    busy_reason: Optional[str] = Field(None, description="Set while a job or library task "
                                                         "blocks clear and move.")
    trash: DiskUsageTrashSummary
    disk_total_bytes: Optional[int] = None
    disk_free_bytes: Optional[int] = None


class DiskUsageClearRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: StrictStr = Field(min_length=1, max_length=1024)
    confirm: StrictBool = False
    expected_size_bytes: StrictInt = Field(ge=0, description="The size the user was shown.")
    expected_file_count: StrictInt = Field(ge=0)
    confirm_irreplaceable: StrictBool = False


class DiskUsageClearDone(BaseModel):
    moved_bytes: int = Field(description="Moved into Trash; nothing is freed until it is "
                                         "deleted from there.")
    file_count: int
    kind: Literal["file", "folder"]
    name: str
    trash_id: str


class DiskUsageMoveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: StrictStr = Field(min_length=1, max_length=1024)
    destination: StrictStr = Field(min_length=1, max_length=1024,
                                   description="An existing folder, full path (PC only).")
    confirm: StrictBool = False


class TempCleanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: Literal[True]


class DiskUsageMoveDone(BaseModel):
    moved_bytes: int
    remaining_bytes: int
    what: Optional[str] = None
    name: str


class DiskUsageTrashItem(BaseModel):
    id: str
    original_path_relative: Optional[str] = Field(
        None, description="Where it came from, relative to the data folder; null when its "
                          "record is missing or damaged.")
    kind: Optional[Literal["file", "folder"]] = None
    size_bytes: Optional[int] = Field(None, description="Null when the walk limit was reached "
                                                        "before this item was measured.")
    file_count: Optional[int] = None
    trashed_at: Optional[str] = None
    restorable: bool


class DiskUsageTrashList(BaseModel):
    items: List[DiskUsageTrashItem]
    size_bytes: int
    item_count: int
    partial: bool
    busy_reason: Optional[str] = None


class DiskUsageTrashRestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: StrictStr = Field(min_length=1, max_length=64)
    confirm: StrictBool = False


class DiskUsageTrashRestoreDone(BaseModel):
    name: str
    kind: Literal["file", "folder"]
    size_bytes: int
    file_count: int


class DiskUsageTrashPurgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: StrictStr = Field(min_length=1, max_length=64)
    confirm_text: StrictStr = Field(max_length=32, description="The word DELETE, exactly.")
    expected_size_bytes: Optional[StrictInt] = Field(
        ..., ge=0, description="The size the user was shown; null only when the list showed "
                               "the size as unknown.")


class DiskUsageTrashPurgeDone(BaseModel):
    freed_bytes: int
    file_count: int


class DiskUsageTrashEmptyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm_text: StrictStr = Field(max_length=32, description="The word DELETE, exactly.")
    expected_item_count: StrictInt = Field(ge=0, description="Items in the Trash list the user saw.")
    expected_size_bytes: StrictInt = Field(ge=0, description="Total size of that list.")


class DiskUsageTrashEmptyDone(BaseModel):
    freed_bytes: int
    removed: int
    failed: int = Field(description="Entries that couldn't be removed; they stay in Trash.")


class UnusedVoiceClip(BaseModel):
    id: str = Field(description="Opaque; only valid until the server restarts.")
    file_type: str = Field(description="wav, mp3, m4a, flac or ogg.")
    size_bytes: int
    modified_at: Optional[str] = None


class UnusedVoiceClipTitle(BaseModel):
    title: str
    size_bytes: int
    clips: List[UnusedVoiceClip]


class UnusedVoiceClipList(BaseModel):
    titles: List[UnusedVoiceClipTitle]
    total_bytes: int
    total_count: int
    titles_in_use: int = Field(description="Titles left out because a dub, narration or "
                                           "audiobook job is running for them.")
    busy_reason: Optional[str] = None


class UnusedVoiceClipChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: StrictStr = Field(min_length=1, max_length=64)
    expected_size_bytes: StrictInt = Field(ge=0, description="The size the user was shown.")


class UnusedVoiceClipTrashRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clips: List[UnusedVoiceClipChoice] = Field(min_length=1, max_length=500)
    confirm: StrictBool = False


class UnusedVoiceClipSkipped(BaseModel):
    id: str
    reason: Literal["no_longer_unused", "changed"]


class UnusedVoiceClipTrashDone(BaseModel):
    moved_count: int
    moved_bytes: int = Field(description="Moved into Trash; nothing is freed until it is "
                                         "deleted from there.")
    skipped: List[UnusedVoiceClipSkipped]
