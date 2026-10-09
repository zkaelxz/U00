"""api/schemas/retranscribe_lines.py -- Review's many-line re-transcription
("Re-transcribe selected") and the untranscribed-gap helpers.
"""

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictInt

__all__ = [
    "RetranscribeManyRequest",
    "RetranscribeManyStarted",
    "RetranscribeManyProposal",
    "RetranscribeManyFailure",
    "RetranscribeManyResult",
    "RetranscribeManyApplyItem",
    "RetranscribeManyApplyRequest",
    "RetranscribeManyApplyResult",
    "TranscribeGap",
    "TranscribeGaps",
    "GapAddLinesRequest",
    "GapAddLinesResult",
]


class RetranscribeManyRequest(BaseModel):
    """line_ids are the ticked lines; the prompt fields resolve as for a full
    transcribe run. The server caps a run (see retranscribe_many_service)."""
    model_config = ConfigDict(extra="forbid")
    line_ids: List[StrictInt] = Field(..., min_length=1, max_length=200)
    initial_prompt: str = Field("", max_length=1000)
    extra_names: str = Field("", max_length=1000)


class RetranscribeManyStarted(BaseModel):
    job_id: str
    drama_id: int
    line_count: int


class RetranscribeManyProposal(BaseModel):
    """Raw line text, as lines.read returns it. had_english: applying this
    proposal clears that line's translation."""
    line_id: int
    number: int
    base_zh: str
    proposed_zh: str
    had_english: bool


class RetranscribeManyFailure(BaseModel):
    """A line that produced no proposal; reason: "empty", "audio_slice" or "line_gone"."""
    line_id: int
    number: int
    reason: str


class RetranscribeManyResult(BaseModel):
    job_id: str
    line_count: int
    proposals: List[RetranscribeManyProposal]
    failures: List[RetranscribeManyFailure]
    unchanged_count: int
    truncated: bool
    device_notice: Optional[str] = None


class RetranscribeManyApplyItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    line_id: StrictInt = Field(..., ge=1)
    expected_zh: str = Field(..., max_length=20000)
    expected_proposed: str = Field(..., min_length=1, max_length=2000)


class RetranscribeManyApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(..., min_length=1, max_length=100)
    items: List[RetranscribeManyApplyItem] = Field(..., min_length=1, max_length=200)


class RetranscribeManyApplyResult(BaseModel):
    applied: List[int]
    skipped: List[int]
    untranslated_count: int


class TranscribeGap(BaseModel):
    """A stretch with no subtitle line. speech: true when the speech coverage
    check found speech here, None when it hasn't run (never false). pieces:
    how many lines "Transcribe this gap" would add."""
    start: float
    end: float
    seconds: float
    pieces: int
    after_line_id: Optional[int] = None
    before_line_id: Optional[int] = None
    speech: Optional[bool] = None


class TranscribeGaps(BaseModel):
    gaps: List[TranscribeGap]
    speech_checked: bool


class GapAddLinesRequest(BaseModel):
    """expected_line_ids: the title's line ids in order, as the client last
    saw them (a different list is a 409, like the other structural edits)."""
    model_config = ConfigDict(extra="forbid")
    expected_line_ids: List[StrictInt]
    start: float = Field(..., ge=0)
    end: float = Field(..., ge=0)
    after_line_id: Optional[int] = Field(None, ge=1, description="None = at the start.")


class GapAddLinesResult(BaseModel):
    new_line_ids: List[int]
    line_ids: List[int]
    split: str
    history_id: Optional[int] = None
    lines_fingerprint: Optional[str] = None
