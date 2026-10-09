"""api/schemas/timing_check.py -- the Review "Check timing" shapes."""

from typing import List, Optional

from pydantic import BaseModel, StrictInt

__all__ = [
    "TimingCheckStarted",
    "TimingCheckLastRun",
    "TimingCheckSuggestion",
    "TimingCheckStatus",
    "TimingSnapRequest",
    "TimingSnapResult",
]


class TimingCheckStarted(BaseModel):
    job_id: str


class TimingCheckLastRun(BaseModel):
    checked_at: str
    flagged: int
    # Set when nothing was judged (no speech in the audio at all).
    notice: Optional[str] = None


class TimingCheckSuggestion(BaseModel):
    """A flagged line's corrected times; start/end are the times the check saw."""
    line_id: int
    start: float
    end: float
    new_start: float
    new_end: float


class TimingCheckStatus(BaseModel):
    job_id: str
    status: str
    progress: Optional[float] = None
    message: str = ""
    result: Optional[dict] = None
    last_check: Optional[TimingCheckLastRun] = None
    suggestions: List[TimingCheckSuggestion] = []


class TimingSnapRequest(BaseModel):
    """line_ids omitted = every flagged line with a suggestion."""
    line_ids: Optional[List[StrictInt]] = None


class TimingSnapResult(BaseModel):
    snapped: int
    stale_ids: List[int]
    history_id: Optional[int] = None
