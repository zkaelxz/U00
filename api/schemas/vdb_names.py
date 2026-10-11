"""api/schemas/vdb_names.py -- /api/vdb-names: the optional personal VTuber
name list. Booleans and counts only; no file path and no source address.
"""

from typing import Optional

from pydantic import BaseModel

__all__ = ["VdbNamesStatus", "TitleVdbNames", "TitleVdbNamesChoice"]


class VdbNamesStatus(BaseModel):
    downloaded: bool
    entry_count: int
    name_count: int
    age_days: Optional[int] = None
    newer_than_30_days: bool


class TitleVdbNames(BaseModel):
    enabled: bool
    downloaded: bool


class TitleVdbNamesChoice(BaseModel):
    enabled: bool
