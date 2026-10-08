"""api/schemas/transcribe.py -- Transcription shapes: transcribe runs and config,
diarization, autotune, re-transcribe and live sessions.
"""

from typing import Annotated, Dict, List, Optional

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
    "SpeechCoverageRunRequest",
    "SpeechCoverageRunResult",
    "SpeechCoverageGap",
    "SpeechCoverageReport",
    "SpeechCoverageStatus",
    "RetranscribeLineRequest",
    "RetranscribeLineResult",
    "RetranscribeApplyRequest",
    "RetranscribeApplyResult",
    "RetranscribeResult",
    "RetimeRunRequest",
    "RetimeProposal",
    "RetimeResult",
    "RetimeApplyItem",
    "RetimeApplyRequest",
    "RetimeApplyResult",
    "CompareSelection",
    "CompareBackendOption",
    "CompareOptions",
    "CompareEstimateRequest",
    "CompareEstimate",
    "CompareRunRequest",
    "CompareRunResult",
    "CompareProposal",
    "CompareResult",
    "CompareApplyItem",
    "CompareApplyRequest",
    "CompareApplyResult",
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
    """Read-only Diarize-stage summary for one drama -- hf_token_configured is a boolean only, never the token value
    itself."""
    drama_id: int
    hf_token_configured: bool
    expected_speakers: Optional[int] = None
    # The speaker-count range the last run used, if any.
    min_speakers: Optional[int] = None
    max_speakers: Optional[int] = None
    # "cuda" or "cpu" -- where the last run's pipeline ran.
    last_device: Optional[str] = None
    audio_available: bool
    manual_speaker_count: int = 0   # hand-corrected speakers
    speaker_summary: Optional[SpeakerTimeSummary] = None   # None: no saved detection


class DiarizationRunResult(BaseModel):
    job_id: str


class SourceConfig(BaseModel):
    """Source-stage config for one drama -- config
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
    """Read-only Transcript-stage summary for one drama -- which action the
    transcribe button would run (from the source config's transcript_mode)
    plus every tuning knob's current value, falling back
    to the defaults the Transcribe stage shows."""
    drama_id: int
    transcript_mode: str
    has_audio_pipeline: bool
    audio_available: bool
    alignment_method: str
    asr_backend_choice: str
    # Set when the saved backend was removed and the default is shown instead.
    asr_backend_notice: Optional[str] = None
    whisper_size: str
    whisper_model_cached: bool
    # Audio seconds per second of work on the last finished run of this model and device.
    measured_speed: Optional[float] = None
    # How many recent runs the measured speed is the median of (0 = none yet).
    measured_speed_runs: int = 0
    # Median seconds per stage (separate, load, decode_vad, transcribe, align) over those runs.
    measured_stage_seconds: Dict[str, float] = Field(default_factory=dict)
    # Audio seconds per second of speaker detection on this device; None until enough runs.
    measured_diarize_speed: Optional[float] = None
    measured_diarize_runs: int = 0
    # False when faster-whisper isn't installed, so a run can't start.
    whisper_installed: bool = True
    beam_size: int
    min_silence_ms: int
    vad_threshold: float
    # "normal" or "sensitive" (see sensitivity_preset.py), and the threshold a run
    # actually uses: the preset lowers an untouched one.
    sensitivity_preset: str = "normal"
    effective_vad_threshold: float
    # Seconds of silence inside a segment that make Whisper skip it; 0 = off.
    hallucination_silence_sec: float
    # Shortest silence between words at which a long line may be cut.
    min_pause_sec: float
    separate_vocals_first: bool
    separation_backend: str
    realign_long_segments: bool
    whisper_fast_mode: bool
    # Whisper's no-repeat and repetition-penalty decoding (off by default).
    whisper_repeat_guard: bool = False
    # Cut lines at sentence ends and word pauses instead of speech-detector pauses.
    split_by_sentences: bool = False
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
    sensitivity_preset: Optional[str] = None
    hallucination_silence_sec: Optional[float] = None
    min_pause_sec: Optional[float] = None
    separate_vocals_first: Optional[bool] = None
    separation_backend: Optional[str] = None
    realign_long_segments: Optional[bool] = None
    whisper_fast_mode: Optional[bool] = None
    whisper_repeat_guard: Optional[bool] = None
    split_by_sentences: Optional[bool] = None
    use_groq: Optional[bool] = None
    hardsub_ocr_backend: Optional[str] = None
    hardsub_interval_sec: Optional[float] = None


class TranscribeRunRequest(BaseModel):
    """transcript_text is required (and only used) when this drama's
    transcript_mode is "have_transcript" -- it's never
    persisted server-side. tesseract_cmd is an optional, client-supplied
    path to the tesseract binary (hardsub_ocr with the "tesseract"
    backend only). The server runs it, so the run route accepts it only from
    the PC itself (403 otherwise); omitted, the path saved in Settings applies."""
    source_language: Optional[str] = None
    chinese_script: Optional[str] = None
    transcript_text: Optional[str] = None
    run_diarize: bool = False
    expected_speakers: Optional[int] = Field(default=None, ge=0, le=20)
    # A speaker-count range for the chained speaker detection
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
# Live capture (polling) -- /api/live/sessions
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
    engine: Optional[str] = None
    model: Optional[str] = None
    progress: float
    cues: List[LiveCue]
    next_index: int


class LiveSessionSummary(BaseModel):
    session_id: str
    status: str
    engine: Optional[str] = None
    model: Optional[str] = None
    cue_count: int


class LiveSessionStopped(BaseModel):
    session_id: str
    stopping: bool


# ---------------------------------------------------------------------------
# Auto-tune speech splitting + glossary from novel
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


# --- Re-transcribe one line ---------------
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


# --- Compare transcription (Review) -----------------------------------------
class CompareSelection(BaseModel):
    """Which lines to compare; the fields each kind reads are named in its
    comment. The server caps a run at CompareOptions.max_lines."""
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(..., pattern="^(line_ids|range|flagged|speaker|time)$")
    line_ids: Optional[List[StrictInt]] = Field(None, max_length=1000)  # line_ids
    from_number: Optional[StrictInt] = Field(None, ge=1)  # range: "from #N"
    to_number: Optional[StrictInt] = Field(None, ge=1)  # range: "to #M"
    speaker: Optional[str] = Field(None, max_length=200)  # speaker
    start_seconds: Optional[float] = Field(None, ge=0)  # time
    end_seconds: Optional[float] = Field(None, ge=0)  # time


class CompareBackendOption(BaseModel):
    id: str
    label: str
    available: bool
    reason: Optional[str] = None


class CompareOptions(BaseModel):
    has_audio: bool
    no_audio_reason: Optional[str] = None
    max_lines: int
    saved_whisper_size: str
    saved_asr_backend: str
    saved_alignment_method: str
    whisper_sizes: List[str]
    backends: List[CompareBackendOption]
    translation_engine: str
    # Why the Qwen3 forced aligner (Re-time) can't run here, or None.
    aligner_reason: Optional[str] = None


class _CompareTranslateFields(BaseModel):
    translate: StrictBool = False
    retranslate_current: StrictBool = False
    engine: Optional[str] = Field(None, max_length=50)
    model: Optional[str] = Field(None, max_length=100)
    gemini_free_tier: Optional[StrictBool] = None
    job_cost_cap_usd: Optional[float] = Field(None, ge=0)


class CompareEstimateRequest(_CompareTranslateFields):
    model_config = ConfigDict(extra="forbid")
    selection: CompareSelection


class CompareEstimate(BaseModel):
    line_count: int
    max_lines: int
    translate: bool
    estimated_usd: Optional[float] = None
    free: bool
    cap_applies: bool
    effective_cap_usd: Optional[float] = None
    monthly_refusal: bool
    estimate_above_cap: bool


class CompareRunRequest(_CompareTranslateFields):
    """whisper_size / asr_backend default to the title's saved values."""
    model_config = ConfigDict(extra="forbid")
    selection: CompareSelection
    whisper_size: Optional[str] = Field(None, max_length=50)
    asr_backend: Optional[str] = Field(None, max_length=50)
    initial_prompt: str = Field("", max_length=1000)
    extra_names: str = Field("", max_length=1000)


class CompareRunResult(BaseModel):
    job_id: str
    drama_id: int
    line_count: int


class CompareProposal(BaseModel):
    line_id: int
    number: int
    start: float
    end: float
    base_zh: str
    base_en: str
    candidate_zh: str
    current_en: str
    candidate_en: str
    translated: bool


class CompareResult(BaseModel):
    job_id: str
    proposals: List[CompareProposal]
    line_count: int
    asr_backend: Optional[str] = None
    whisper_size: Optional[str] = None
    translated: bool
    partial: bool
    cap_reached: bool
    errors: List[str]


class CompareApplyItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    line_id: StrictInt = Field(..., ge=1)
    expected_base_zh: str = Field(..., max_length=20000)
    expected_candidate_zh: str = Field(..., min_length=1, max_length=2000)
    use_english: StrictBool = False
    expected_candidate_en: str = Field("", max_length=2000)


class CompareApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(..., min_length=1, max_length=100)
    items: List[CompareApplyItem] = Field(..., min_length=1, max_length=200)


class CompareApplyResult(BaseModel):
    applied: List[int]
    skipped: List[int]


# --- Re-time with the Qwen3 aligner (Review) ---------------------------------
class RetimeRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    line_ids: List[StrictInt] = Field(..., min_length=1, max_length=1000)


class RetimeProposal(BaseModel):
    line_id: int
    number: int
    base_zh: str
    start: float
    end: float
    new_start: float
    new_end: float
    uncertain: bool


class RetimeResult(BaseModel):
    job_id: str
    proposals: List[RetimeProposal]
    line_count: int
    partial: bool
    device: Optional[str] = None
    device_notice: Optional[str] = None
    errors: List[str]


class RetimeApplyItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    line_id: StrictInt = Field(..., ge=1)
    expected_new_start: float
    expected_new_end: float


class RetimeApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(..., min_length=1, max_length=100)
    items: List[RetimeApplyItem] = Field(..., min_length=1, max_length=200)


class RetimeApplyResult(CompareApplyResult):
    overlapping: List[int] = []


class SpeechCoverageRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min_gap_seconds: float = Field(default=2.0, ge=0.5, le=30.0)


class SpeechCoverageRunResult(BaseModel):
    job_id: str


class SpeechCoverageGap(BaseModel):
    """A stretch with speech and no subtitle line. raw_status: "lost_after"
    (the raw transcript has text here), "none" (it has none), "unknown" (no
    raw transcript for the title)."""
    start: float
    end: float
    seconds: float
    speech_seconds: float
    raw_status: str
    raw_text: str = ""
    after_line_id: Optional[int] = None
    before_line_id: Optional[int] = None


class SpeechCoverageReport(BaseModel):
    audio_seconds: Optional[float] = None
    speech_seconds: Optional[float] = None
    covered_seconds: Optional[float] = None
    covered_percent: Optional[float] = None
    vad_threshold: Optional[float] = None
    min_gap_seconds: Optional[float] = None
    raw_available: bool = False
    gaps_total: int = 0
    gaps: List[SpeechCoverageGap] = []
    failed_reason: Optional[str] = None
    detail: Optional[str] = None


class SpeechCoverageStatus(BaseModel):
    """This title's coverage check as held in this app session; status "idle" when none."""
    job_id: str
    status: str
    progress: Optional[float] = None
    message: str = ""
    result: Optional[SpeechCoverageReport] = None
