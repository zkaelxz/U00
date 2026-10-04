"""api/schemas/transcribe.py -- Transcription shapes: transcribe runs and config,
diarization, autotune, re-transcribe and live sessions.
"""

from typing import Annotated, List, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

__all__ = [
    "SpeakerTime",
    "SpeakerTimeSummary",
    "DiarizationConfig",
    "DiarizationRunResult",
    "SourceConfig",
    "SourceConfigUpdate",
    "TranscribeConfig",
    "TranscribeConfigUpdate",
    "TranscribeRunRequest",
    "TranscribeRunResult",
    "LiveSessionStart",
    "LiveSessionStarted",
    "LiveCue",
    "LiveSessionStatus",
    "LiveSessionSummary",
    "LiveSessionStopped",
    "AutotuneCandidateMs",
    "AutotuneRunRequest",
    "AutotuneRunResult",
    "AutotuneCandidateScore",
    "AutotuneStatus",
    "AutotuneApplyRequest",
    "RetranscribeLineRequest",
    "RetranscribeLineResult",
    "RetranscribeApplyRequest",
    "RetranscribeApplyResult",
    "RetranscribeResult",
]


class SpeakerTime(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str
    seconds: float
    percent: float
    turns: int


class SpeakerTimeSummary(BaseModel):
    """Per-speaker share of the saved detection's turns (no paths)."""
    model_config = ConfigDict(extra="forbid")
    speakers: List[SpeakerTime]
    total_speech_seconds: float
    uncovered_seconds: Optional[float] = None


class DiarizationConfig(BaseModel):
    """Read-only Diarize-stage summary for one drama (Migration Slice
    16) -- hf_token_configured is a boolean only, never the token value
    itself (D2)."""
    drama_id: int
    hf_token_configured: bool
    expected_speakers: Optional[int] = None
    # Step 105: the speaker-count range the last run used, if any.
    min_speakers: Optional[int] = None
    max_speakers: Optional[int] = None
    # Step 101: "cuda" or "cpu" -- where the last run's pipeline ran.
    last_device: Optional[str] = None
    audio_available: bool
    manual_speaker_count: int = 0   # parity D06: hand-corrected speakers
    speaker_summary: Optional[SpeakerTimeSummary] = None   # None: no saved detection


class DiarizationRunResult(BaseModel):
    job_id: str


class SourceConfig(BaseModel):
    """Source-stage config for one drama (Migration Slice 19) -- config
    only (language/script/content mode/transcript mode) plus read-only
    audio/video/transcript-source presence. Never includes an upload or
    a secret."""
    drama_id: int
    source_language: str
    chinese_script: str
    content_mode: str
    has_audio_pipeline: bool
    audio_available: bool
    has_video_source: bool
    transcript_mode: str
    transcript_mode_options: List[str]
    has_raw_novel_context: bool


class SourceConfigUpdate(BaseModel):
    """All fields optional -- only what's passed is validated and
    written (a field-scoped partial update, matching db.update_drama's
    own shape)."""
    source_language: Optional[str] = None
    chinese_script: Optional[str] = None
    content_mode: Optional[str] = None
    transcript_mode: Optional[str] = None


class TranscribeConfig(BaseModel):
    """Read-only Transcript-stage summary for one drama (Migration Slice
    20) -- which action the transcribe button would run (from Slice 19's
    transcript_mode) plus every tuning knob's current value, falling back
    to the defaults the Transcribe stage shows."""
    drama_id: int
    transcript_mode: str
    has_audio_pipeline: bool
    audio_available: bool
    alignment_method: str
    asr_backend_choice: str
    whisper_size: str
    whisper_model_cached: bool
    # Audio seconds per second of work on the last finished run of this model and device.
    measured_speed: Optional[float] = None
    # How many recent runs the measured speed is the median of (0 = none yet).
    measured_speed_runs: int = 0
    # False when faster-whisper isn't installed, so a run can't start.
    whisper_installed: bool = True
    beam_size: int
    min_silence_ms: int
    vad_threshold: float
    separate_vocals_first: bool
    separation_backend: str
    realign_long_segments: bool
    whisper_fast_mode: bool
    use_groq: bool
    has_video_source: bool
    hardsub_ocr_backend: str
    hardsub_interval_sec: float
    # Whisper prompt built from the series glossary and raw-novel excerpt;
    # a run with an empty initial_prompt uses this.
    auto_initial_prompt: str = ""


class TranscribeConfigUpdate(BaseModel):
    """All fields optional -- only what's passed is validated and
    written."""
    whisper_size: Optional[str] = None
    alignment_method: Optional[str] = None
    asr_backend_choice: Optional[str] = None
    beam_size: Optional[int] = None
    min_silence_ms: Optional[int] = None
    vad_threshold: Optional[float] = None
    separate_vocals_first: Optional[bool] = None
    separation_backend: Optional[str] = None
    realign_long_segments: Optional[bool] = None
    whisper_fast_mode: Optional[bool] = None
    use_groq: Optional[bool] = None
    hardsub_ocr_backend: Optional[str] = None
    hardsub_interval_sec: Optional[float] = None


class TranscribeRunRequest(BaseModel):
    """transcript_text is required (and only used) when this drama's
    transcript_mode is "have_transcript" -- per Slice 19, it's never
    persisted server-side. tesseract_cmd is an optional, client-supplied
    path to the tesseract binary (hardsub_ocr with the "tesseract"
    backend only). The server runs it, so the run route accepts it only from
    the PC itself (403 otherwise); omitted, the path saved in Settings applies."""
    source_language: Optional[str] = None
    chinese_script: Optional[str] = None
    transcript_text: Optional[str] = None
    run_diarize: bool = False
    expected_speakers: Optional[int] = Field(default=None, ge=0, le=20)
    # Step 105: a speaker-count range for the chained speaker detection
    # (pyannote min_speakers/max_speakers); not combined with expected_speakers.
    min_speakers: Optional[int] = Field(default=None, ge=0, le=20)
    max_speakers: Optional[int] = Field(default=None, ge=0, le=20)
    # Non-empty: replaces the automatic prompt entirely. Empty: the server
    # builds glossary names + extra_names + raw-novel excerpt.
    initial_prompt: str = ""
    extra_names: str = Field("", max_length=1000)
    tesseract_cmd: Optional[str] = None


class TranscribeRunResult(BaseModel):
    job_id: str


# ---------------------------------------------------------------------------
# API batch 1: Live capture (spec L-1, polling) -- /api/live/sessions
# ---------------------------------------------------------------------------
class LiveSessionStart(BaseModel):
    """Keys are resolved server-side; no browser cookies over the API.
    Numbers are clamped to the service's ranges (segment 10-60 s, overlap
    0-8 s and at most half the segment, max_minutes 1-240)."""
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=2000)
    source_language: str = Field("zh", max_length=5)
    whisper_size: str = Field("small", max_length=10)
    segment_seconds: float = 20
    overlap_seconds: float = 3
    engine: Optional[str] = Field(None, max_length=40,
                                  description="None = the Settings default engine (checked as paid).")
    model: Optional[str] = Field(None, max_length=100)
    max_minutes: float = 60
    use_gpu: StrictBool = False


class LiveSessionStarted(BaseModel):
    session_id: str


class LiveCue(BaseModel):
    start: float
    end: float
    text: str
    translated: str


class LiveSessionStatus(BaseModel):
    session_id: str
    status: str   # queued | running | done | error | cancelled
    message: str
    progress: float
    cues: List[LiveCue]
    next_index: int


class LiveSessionSummary(BaseModel):
    session_id: str
    status: str
    engine: Optional[str] = None
    cue_count: int


class LiveSessionStopped(BaseModel):
    session_id: str
    stopping: bool


# ---------------------------------------------------------------------------
# Route batch 2C: auto-tune speech splitting + glossary from novel
# ---------------------------------------------------------------------------
AutotuneCandidateMs = Annotated[StrictInt, Field(ge=300, le=3000)]


class AutotuneRunRequest(BaseModel):
    """candidates default to core.DEFAULT_AUTOTUNE_CANDIDATES_MS; 1-6
    distinct values (the service rejects duplicates)."""
    model_config = ConfigDict(extra="forbid")
    candidates: Optional[List[AutotuneCandidateMs]] = Field(None, min_length=1, max_length=6)
    initial_prompt: str = Field("", max_length=1000)
    extra_names: str = Field("", max_length=1000)


class AutotuneRunResult(BaseModel):
    job_id: str
    candidates: List[int]


class AutotuneCandidateScore(BaseModel):
    candidate_ms: int
    long_lines: int
    total_lines: int


class AutotuneStatus(BaseModel):
    """This drama's auto-tune job as held in this app session. results /
    best_candidate_ms only once status is "done". No run held: status "idle"."""
    job_id: str
    status: str
    progress: Optional[float] = None
    message: str = ""
    results: Optional[List[AutotuneCandidateScore]] = None
    best_candidate_ms: Optional[int] = None


class AutotuneApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_ms: AutotuneCandidateMs


# --- Re-transcribe one line (parity audit B1, inventory R23) ---------------
class RetranscribeLineRequest(BaseModel):
    """Optional body. Same prompt rules as TranscribeRunRequest: a non-empty
    initial_prompt replaces the automatic prompt; otherwise the server uses
    glossary names + extra_names + raw-novel excerpt."""
    model_config = ConfigDict(extra="forbid")
    initial_prompt: str = Field("", max_length=1000)
    extra_names: str = Field("", max_length=1000)


class RetranscribeLineResult(BaseModel):
    job_id: str
    drama_id: int
    line_id: int


class RetranscribeApplyRequest(BaseModel):
    """"Use this": job_id is the finished re-transcription; expected_zh and
    expected_proposed are the base_zh and proposed_zh that
    GET .../retranscribe showed. Anything else, or a line changed since the
    job started, is a 409 (nothing written)."""
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(..., min_length=1, max_length=100)
    expected_zh: str = Field(..., max_length=20000)
    expected_proposed: str = Field(..., min_length=1, max_length=2000)


class RetranscribeApplyResult(BaseModel):
    drama_id: int
    line_id: int
    zh: str


class RetranscribeResult(BaseModel):
    """A finished re-transcription's proposal for one line, raw (held in this
    API process only; gone after a restart)."""
    job_id: str
    line_id: int
    status: str
    proposed_zh: str
    base_zh: str
