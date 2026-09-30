"""
api/sources_import_schemas.py -- response models for the Step 107 import
state route (GET /api/sources/{name}/import-state). Kept out of
api/schemas.py so this step could be built alongside another branch
editing that file; the shared ErrorResponse still lives there. Text is
scrubbed by the service; no URL field exists on any model.
"""

from typing import List, Literal

from pydantic import BaseModel


class SourcesImportRetryRow(BaseModel):
    chapter_id: str
    title: str
    status: Literal["failed", "not_attempted", "partial"]
    error: str


class SourcesImportState(BaseModel):
    """Chapters of one series already imported into one drama, and the
    ones the last imports left failed or not attempted. `retry` also lists
    "partial" chapters (interrupted mid-write: check before re-importing);
    `retry_count` counts only the retryable ones."""
    source: str
    series_id: str
    drama_id: int
    imported_chapter_ids: List[str]
    retry: List[SourcesImportRetryRow]
    retry_count: int
