"""api/schemas/disk_usage.py -- the per-title size split in the Disk usage
scan (services/disk_usage_title_sizes.py) and the previous library kept
beside the library folder. Numbers only: no path, file name or URL."""

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

__all__ = ["DiskUsageTitleSizes", "PreviousLibrary", "PreviousLibraryDeleteRequest",
           "PreviousLibraryDeleted"]


class DiskUsageTitleSizes(BaseModel):
    originals_bytes: int = Field(description="Source media, uploaded files, replaced media kept "
                                             "aside, page scans and the raw novel text.")
    raw_transcript_bytes: int = Field(description="Every transcription run's untouched output, "
                                                  "plain or compressed.")
    history_bytes: int = Field(description="Line-history snapshots, as stored in the database.")
    regenerable_bytes: int = Field(description="Files the storage presets may remove and Baihe "
                                               "can rebuild.")
    complete: bool = Field(description="False when a limit cut the folder walk short (the file "
                                       "sizes are lower bounds).")


class PreviousLibrary(BaseModel):
    """The copies of the library kept beside it (services/previous_library_service.py)."""
    present: bool
    label: str
    size_bytes: int
    file_count: int
    folder_count: int
    partial: bool = Field(description="True when a walk limit cut the measurement short.")


class PreviousLibraryDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm_text: StrictStr = Field(max_length=32, description="The word DELETE, exactly.")
    expected_size_bytes: StrictInt = Field(ge=0, description="The size the user was shown.")


class PreviousLibraryDeleted(BaseModel):
    deleted: int
    freed_bytes: int
