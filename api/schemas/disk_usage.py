"""api/schemas/disk_usage.py -- the per-title size split in the Disk usage
scan (services/disk_usage_title_sizes.py). Numbers only: no path, file name
or URL."""

from pydantic import BaseModel, Field

__all__ = ["DiskUsageTitleSizes"]


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
