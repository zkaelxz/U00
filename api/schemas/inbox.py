"""api/schemas/inbox.py -- /api/inbox: the watch-folder settings and status. The folder's path is
write-only; a read returns only whether one is set and its last segment."""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

__all__ = ["InboxState", "InboxUpdate"]


class InboxState(BaseModel):
    on: bool
    folder_set: bool
    folder_name: str
    source_language: Literal["zh", "ja", "ko"]
    series_id: Optional[int] = None
    move: bool
    imported: int
    last_title_id: Optional[int] = None
    message: str


class InboxUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    on: StrictBool
    # Blank keeps the saved folder.
    path: StrictStr = Field("", max_length=1024)
    source_language: Literal["zh", "ja", "ko"]
    series_id: Optional[StrictInt] = Field(None, ge=1, le=2**31 - 1)
    move: StrictBool = False
