"""api/schemas/voice.py -- Voice shapes: dubbing, narration and voice-clone
setup.
"""

from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool

__all__ = [
    "DubTtsEngine",
    "DubSpeaker",
    "DubDefaults",
    "DubConfig",
    "DubPacingLine",
    "DubPacing",
    "NarrationEngineOption",
    "NarrationConfig",
    "NarrationRunRequest",
    "NarrationRunResult",
    "DubRunRequest",
    "DubRunStarted",
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
    """One selectable voice engine for the Dub stage."""
    key: str
    label: str
    # Fixed text saying what this engine needs that is missing here (its
    # package, or ffmpeg); None when it can run.
    unavailable_reason: Optional[str] = None


class DubSpeaker(BaseModel):
    """One speaker's resolved engine, as the Generate button would resolve
    it. D2: no reference-audio path, only a boolean."""
    speaker_label: str
    character_name: Optional[str] = None
    engine: str
    has_clone_ref: bool
    # Voice-clone setup: why this speaker won't be cloned as configured
    # (e.g. a clone engine with no clip or voice design falls back to the
    # engine picked in Dub, or its stored engine was removed).
    clone_warning: Optional[str] = None


class DubDefaults(BaseModel):
    """Pacing-limit defaults and slider ranges; null for narration."""
    max_speedup: float
    max_slowdown: float
    speedup_range: List[float]
    slowdown_range: List[float]


class DubConfig(BaseModel):
    """Read-only Dub-stage summary for one drama.
    D2: no filesystem path or secret."""
    drama_id: int
    content_mode: Optional[str] = None
    is_narration: bool
    narration_language: str
    narration_language_options: List[str]
    source_language: str
    tts_engines: List[DubTtsEngine]
    default_engine: str
    # Plain reason nothing can be generated whatever engine is picked (no
    # engine installed, or a character stored with a removed engine).
    blocker: Optional[str] = None
    defaults: Optional[DubDefaults] = None
    speakers: List[DubSpeaker]
    gpu_required: bool
    speakable_line_count: int
    track_available: bool
    can_keep_background: bool = False


class DubPacingLine(BaseModel):
    """One line's fit against its original timing window. status is
    "fit", "stretched" or "overflow"."""
    idx: int
    status: str
    factor: float
    clip_ms: Optional[float] = None
    window_ms: Optional[float] = None


class DubPacing(BaseModel):
    """Per-line pacing from the last dub run; `available` is False when
    there is nothing to show (never dubbed, or narration). D2: no paths."""
    available: bool
    counts: Dict[str, int]
    lines: List[DubPacingLine]


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


class DubRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tts_engine: str = Field(default="omnivoice", max_length=40)
    max_speedup: Optional[float] = Field(default=None, ge=1.0, le=2.0)
    max_slowdown: Optional[float] = Field(default=None, ge=0.5, le=1.0)
    narration_language: Optional[str] = Field(default=None, max_length=20)
    keep_background: bool = False


class DubRunStarted(BaseModel):
    job_id: str


# Voice-clone setup (parity audit blocker #7; inventory C01, C03, C09, C13):
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
