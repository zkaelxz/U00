"""api/schemas/voice.py -- Voice shapes: dubbing, narration and voice-clone
setup.
"""

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool

__all__ = [
    "DubTtsEngine",
    "NarrationEngineOption",
    "NarrationConfig",
    "NarrationRunRequest",
    "NarrationRunResult",
    "DubRunStarted",
    "ListenConfig",
    "ListenRunRequest",
    "VoiceCloneExtractRequest",
    "VoiceCloneJobStarted",
    "VoiceCloneCandidate",
    "VoiceCloneSpeakerCandidates",
    "VoiceCloneCandidates",
    "VoiceCloneRemoveRequest",
    "VoiceCloneBankSaveRequest",
    "VoiceCloneSeriesLinkRequest",
]


class DubTtsEngine(BaseModel):
    """One selectable voice engine for Listen."""
    key: str
    label: str
    # Fixed text saying what this engine needs that is missing here (its
    # package, or ffmpeg); None when it can run.
    unavailable_reason: Optional[str] = None


class NarrationEngineOption(BaseModel):
    key: str
    key_configured: bool


class NarrationConfig(BaseModel):
    drama_id: int
    is_narration: bool
    has_novel_source: bool
    engines: List[NarrationEngineOption]
    default_engine: str
    max_chunk_chars: int
    existing_line_count: int
    replaces_existing_lines: bool
    job_running: bool


class NarrationRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    engine: Optional[str] = Field(default=None, max_length=40)
    model: Optional[str] = Field(default=None, max_length=200)


class NarrationRunResult(BaseModel):
    job_id: str


class DubRunStarted(BaseModel):
    job_id: str


class ListenConfig(BaseModel):
    """What the Reader's Listen needs for a narrated title. No path or secret."""
    drama_id: int
    narration_language: str
    narration_language_options: List[str]
    tts_engines: List[DubTtsEngine]
    default_engine: str
    blocker: Optional[str] = None
    gpu_required: bool
    speakable_line_count: int
    track_available: bool


class ListenRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tts_engine: str = Field(default="omnivoice", max_length=40)
    narration_language: Optional[str] = Field(default=None, max_length=20)


# Voice-clone setup:
# services/voice_clone_service.py. No path, filename or URL anywhere.
# ---------------------------------------------------------------------------
class VoiceCloneExtractRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    speaker_label: str = Field(min_length=1, max_length=200)
    max_candidates: int = Field(3, ge=1, le=5)


class VoiceCloneJobStarted(BaseModel):
    job_id: str


class VoiceCloneCandidate(BaseModel):
    """An opaque candidate id (for preview/choose), its time window in the
    drama's audio and the transcript line matched to it ("" if none)."""
    id: str
    start: float
    end: float
    duration: float
    ref_text: str


class VoiceCloneSpeakerCandidates(BaseModel):
    """skip_reason ("too_short", "too_long", "no_segments") and
    closest_duration explain an extraction that found nothing."""
    speaker_label: str
    candidates: List[VoiceCloneCandidate]
    skip_reason: Optional[str] = None
    closest_duration: Optional[float] = None


class VoiceCloneCandidates(BaseModel):
    drama_id: int
    speakers: List[VoiceCloneSpeakerCandidates]


class VoiceCloneRemoveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    speaker_label: str = Field(min_length=1, max_length=200)
    confirm: StrictBool = False


class VoiceCloneBankSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    speaker_label: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    notes: str = Field("", max_length=1000)


class VoiceCloneSeriesLinkRequest(BaseModel):
    """series_character_id is required; null unlinks."""
    model_config = ConfigDict(extra="forbid")
    speaker_label: str = Field(min_length=1, max_length=200)
    series_character_id: Optional[int] = Field(..., ge=1, le=2147483647)
