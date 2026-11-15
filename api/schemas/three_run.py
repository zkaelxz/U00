"""api/schemas/three_run.py -- Review's "Run 3 times, review differences" (pick one of three runs per differing line)."""

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictInt

__all__ = [
    "ThreeRunBegin",
    "ThreeRunBegun",
    "ThreeRunCapture",
    "ThreeRunItem",
    "ThreeRunState",
    "ThreeRunPick",
    "ThreeRunPicks",
    "ThreeRunKeepRest",
    "ThreeRunPickResult",
]


class ThreeRunBegin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    line_ids: Optional[List[StrictInt]] = Field(None, max_length=5000)


class ThreeRunBegun(BaseModel):
    line_ids: List[int]
    history_id: int
    runs_done: int


class ThreeRunCapture(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: StrictInt = Field(ge=1, le=3)


class ThreeRunItem(BaseModel):
    line_id: int
    idx: int
    zh: str
    versions: List[str]
    picked_run: Optional[int] = None
    current_en: str


class ThreeRunState(BaseModel):
    active: bool
    runs_done: int
    history_id: Optional[int] = None
    line_count: int
    items: List[ThreeRunItem]


class ThreeRunPick(BaseModel):
    model_config = ConfigDict(extra="forbid")
    line_id: StrictInt
    run: StrictInt = Field(ge=1, le=3)


class ThreeRunPicks(BaseModel):
    model_config = ConfigDict(extra="forbid")
    picks: List[ThreeRunPick] = Field(min_length=1, max_length=5000)


class ThreeRunKeepRest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: StrictInt = Field(ge=1, le=3)


class ThreeRunPickResult(BaseModel):
    applied: List[int]
    skipped: List[int]
