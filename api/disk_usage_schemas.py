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


class DiskUsageScan(BaseModel):
    path: str
    parent: Optional[str] = None
    total_bytes: int
    file_count: int
    items: List[DiskUsageItem]
    partial: bool = Field(description="A walk limit (entries or time) was hit: sizes are "
                                      "lower bounds.")
    partial_reason: Optional[Literal["entries", "time", "items"]] = None
    not_shown: int = Field(0, description="Items left out because the folder holds more than "
                                          "the list limit or the walk limit was hit.")
    scanned_entries: int
    busy_reason: Optional[str] = Field(None, description="Set while a job or library task "
                                                         "blocks clear and move.")
    recycle_available: bool
    recycle_reason: Optional[str] = Field(None, description="Why Clear is unavailable "
                                                            "(no Recycle Bin, or Baihe is "
                                                            "running as a Windows service).")
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
    freed_bytes: int
    file_count: int
    kind: Literal["file", "folder"]
    name: str


class DiskUsageMoveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: StrictStr = Field(min_length=1, max_length=1024)
    destination: StrictStr = Field(min_length=1, max_length=1024,
                                   description="An existing folder, full path (PC only).")
    confirm: StrictBool = False


class DiskUsageMoveDone(BaseModel):
    moved_bytes: int
    remaining_bytes: int
    what: Optional[str] = None
    name: str
