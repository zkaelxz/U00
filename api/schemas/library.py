"""api/schemas/library.py -- Library shapes: dramas, series, media and export
(bulk actions, backup and storage are in library_admin.py).
"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, model_validator

from api.schemas.common import AssStyleOverrides, ConfirmRequest, DramaDetail, DramaSummary, LibraryPreset

__all__ = [
    "CaptureNote",
    "CaptureNoteList",
    "SameTitle",
    "SameTitleList",
    "SameTitleQuery",
    "DramaSeriesUpdate",
    "DramaListResponse",
    "ExportReadiness",
    "FlagActionResult",
    "ReadingSpeedMode",
    "ReadingSpeedModeUpdate",
    "ClearReadingSpeedFlagsResult",
    "AutoQcFlagResult",
    "AssStyleOptions",
    "DramaCreateRequest",
    "DramaMetadataUpdate",
    "DramaPresetDefaults",
    "DramaCreateResult",
    "DramaDeleteResult", "OwnTitleDelete",
    "MediaUploadResult",
    "MediaStatus",
    "MediaPeaks",
    "MediaShotChanges",
    "UploadAndTranscribeResult",
    "LibraryUsage",
    "LibraryDashboard",
    "LibraryDramaRef",
    "LibraryRecentResponse",
    "LibraryCostRow",
    "LibraryCostResponse",
    "LibrarySeries",
    "LibrarySeriesResponse",
    "LibrarySearchHit",
    "LibrarySearchResponse",
    "LibraryHistoryEntry",
    "LibraryHistoryResponse",
    "LibraryPresetsResponse",
    "LibraryVoice",
    "LibraryVoiceBankResponse",
    "LibraryVoiceDetails",
    "LibraryRename",
    "MediaAnalysis",
    "MediaSubtitleTrack",
    "AutofillRequest",
    "AutofillSuggestion",
    "AutofillApply",
    "MediaExportStarted",
    "SoftsubVideoRequest",
    "DubbedVideoRequest",
    "MarkExportedResult",
    "WorkflowStageState",
    "WorkflowProgress",
    "DeleteConfirm",
    "MediaRemoveResult",
    "RawNovelRemoveResult",
    "PresetDeleteResult",
    "VoiceBankDeleteResult",
    "MediaUrlDownloadRequest",
    "MediaUrlDownloadStarted",
    "LibraryContinueEntry",
    "LibraryContinueResponse",
    "LibraryFilterOptions",
    "RomanizeCreditsRequest",
    "RomanizeCreditsResult",
    "CoverArtResult",
    "DramaSourceLink",
    "DramaFolderOpened",
    "BurnPreviewStart",
    "BurnPreviewStarted",
    "BurnPreviewClip",
    "BurnPreviewInfo",
]


class CaptureNote(BaseModel):
    summary: str
    created_at: str


class CaptureNoteList(BaseModel):
    items: List[CaptureNote] = Field(description="Newest first, at most 5.")


class DramaListResponse(BaseModel):
    items: List[DramaSummary]
    count: int


class ExportReadiness(BaseModel):
    """Read-only export-readiness summary for one drama
    -- counts only, never flags a line or generates a file."""
    drama_id: int
    total_lines: int
    zh_filled: int
    en_filled: int
    fully_translated: bool
    overlap_count: int
    auto_qc_issue_count: int
    dense_line_count: int


class FlagActionResult(BaseModel):
    """A flagging action's result: 0 is not an
    error, just nothing new to flag."""
    flagged_count: int


class ReadingSpeedMode(BaseModel):
    mode: Literal["normal", "relaxed", "off"]


class ReadingSpeedModeUpdate(BaseModel):
    mode: Literal["normal", "relaxed", "off"]


class ClearReadingSpeedFlagsResult(BaseModel):
    """flagged_count is the re-check's count (0 unless recheck was asked
    for); history_id is None when no line carried the flag."""
    cleared_count: int
    flagged_count: int
    history_id: Optional[int] = None


class AutoQcFlagResult(BaseModel):
    """auto_qc.run_auto_qc's own counts; see its
    docstring for exactly what each counts."""
    flagged: int
    cleared: int
    already_flagged: int
    checked: int


class AssStyleOptions(BaseModel):
    presets: Dict[str, Dict[str, Any]]
    default_preset: str
    fonts: List[str]
    custom_font_allowed: bool
    alignments: Dict[str, int]
    size_range: List[int]
    outline_width_range: List[int]
    shadow_range: List[int]


class DramaCreateRequest(BaseModel):
    """Create a drama. `source_language` is required
    (zh/ja/ko); `series_id` and `new_series_name` are mutually exclusive."""
    model_config = ConfigDict(extra="forbid")
    source_language: str
    title_en: str = Field(default="", max_length=300)
    title_zh: str = Field(default="", max_length=300)
    author: str = Field(default="", max_length=300)
    studio: str = Field(default="", max_length=300)
    director: str = Field(default="", max_length=300)
    voice_actors: str = Field(default="", max_length=300)
    summary: str = Field(default="", max_length=5000)
    media_type: str = "audio_drama"
    release_date: str = Field(default="", max_length=10)
    series_id: Optional[int] = Field(default=None, ge=1, le=2147483647)
    new_series_name: Optional[str] = Field(default=None, max_length=300)
    preset_id: Optional[int] = Field(default=None, ge=1, le=2147483647)
    # "Link (optional)": stored as the title's source link; with no name
    # typed, the server names the title from it.
    source_url: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def _needs_a_title(self):
        # Here, not in drama_service: importers create untitled
        # rows through the service on purpose.
        if not (self.title_en.strip() or self.title_zh.strip() or self.source_url.strip()):
            raise ValueError("A title or a link is required.")
        return self


class SameTitleQuery(BaseModel):
    """What an add form has typed so far, to ask which titles already carry it."""
    model_config = ConfigDict(extra="forbid")
    title_en: str = Field(default="", max_length=300)
    title_zh: str = Field(default="", max_length=300)
    source_url: str = Field(default="", max_length=2000)


class SameTitle(BaseModel):
    id: int
    title: str
    same: Literal["link", "name"]


class SameTitleList(BaseModel):
    items: List[SameTitle]


class DramaSeriesUpdate(BaseModel):
    """Put a title in a series and nothing else: `series_id` (0 takes it out
    of its series) or `new_series_name`, exactly one."""
    model_config = ConfigDict(extra="forbid")
    series_id: Optional[int] = Field(default=None, ge=0, le=2147483647)
    new_series_name: Optional[str] = Field(default=None, max_length=300)


class DramaMetadataUpdate(BaseModel):
    """Partial metadata update: only fields present in the body are applied.
    Unknown keys (status, content_mode, *_filename, ...) are rejected. For
    `chapter_count`/`episode_number`, 0 clears the value; `series_id` 0
    takes the drama out of its series. `new_series_name` ("+ New series")
    moves it into the series of that name, created for the caller if none
    exists; not together with `series_id`."""
    model_config = ConfigDict(extra="forbid")
    title_en: Optional[str] = Field(default=None, max_length=300)
    title_zh: Optional[str] = Field(default=None, max_length=300)
    author: Optional[str] = Field(default=None, max_length=300)
    studio: Optional[str] = Field(default=None, max_length=300)
    director: Optional[str] = Field(default=None, max_length=300)
    voice_actors: Optional[str] = Field(default=None, max_length=300)
    summary: Optional[str] = Field(default=None, max_length=5000)
    genre: Optional[str] = Field(default=None, max_length=300)
    custom_tags: Optional[str] = Field(default=None, max_length=2000)
    source_url: Optional[str] = Field(default=None, max_length=2000)
    episode_summary: Optional[str] = Field(default=None, max_length=5000)
    episode_brief: Optional[str] = Field(default=None, max_length=5000)
    project_instructions: Optional[str] = Field(default=None, max_length=5000)
    chapter_count: Optional[int] = Field(default=None, ge=0, le=2147483647)
    episode_number: Optional[int] = Field(default=None, ge=0, le=2147483647)
    default_female_pronouns: Optional[StrictBool] = None
    include_genre_notes: Optional[StrictBool] = None
    keep_original: Optional[StrictBool] = None
    translate_by_sentence: Optional[StrictBool] = None
    media_type: Optional[str] = None
    release_date: Optional[str] = Field(default=None, max_length=10)
    publication_status: Optional[str] = None
    series_id: Optional[int] = Field(default=None, ge=0, le=2147483647)
    new_series_name: Optional[str] = Field(default=None, max_length=300)


class DramaPresetDefaults(BaseModel):
    """A preset's session-only values, returned for the client to hold
    (only the preset's translation engine is persisted on the drama)."""
    style_preset: Optional[str] = None
    locale: Optional[str] = None
    default_female_pronouns: bool
    include_genre_notes: bool
    engine_model: Optional[str] = None  # applies to the drama's saved translation_engine


class DramaCreateResult(DramaDetail):
    preset_defaults: Optional[DramaPresetDefaults] = None
    # Profile settings left out because they don't suit the title's language.
    profile_skipped: List[str] = Field(default_factory=list)
    # With a link and no name: "link" (the link's own title) or
    # "placeholder" (the link's host, for the owner to rename).
    title_from_link: Optional[Literal["link", "placeholder"]] = None


class OwnTitleDelete(ConfirmRequest):
    """Deleting a title you made, from any device: confirm=true and the word DELETE."""
    confirm_text: StrictStr = Field("", max_length=32, description="The word DELETE, exactly.")


class DramaDeleteResult(BaseModel):
    deleted: bool
    drama_id: int
    warning: Optional[str] = None


class MediaUploadResult(BaseModel):
    # None for a video: its name is picked when the extraction job puts it in place.
    name: Optional[str] = None
    size: int
    kind: str
    # Set for a video -- the background audio-extraction job to poll.
    job_id: Optional[str] = None


class MediaStatus(BaseModel):
    drama_id: int
    has_audio: bool
    has_source_video: bool
    # Transcript mode is hardsub_ocr: replacing the video with audio switches it.
    reads_burned_in_subtitles: bool = False
    upload_max_mb: int
    # Superseded originals and failed uploads kept in the title's folder.
    kept_media_files: int = 0
    kept_media_bytes: int = 0


class MediaPeaks(BaseModel):
    start: float
    end: float
    buckets: int
    peaks: List[int] = Field(description="Peak loudness per bucket, 0-255.")


class MediaShotChanges(BaseModel):
    start: float
    end: float
    cuts: List[float] = Field(description="Shot-change times in seconds, ascending, inside the window.")


class UploadAndTranscribeResult(BaseModel):
    upload: MediaUploadResult
    job_id: str


class LibraryUsage(BaseModel):
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    estimated_cost_usd: float
    call_count: int


class LibraryDashboard(BaseModel):
    total_dramas: int
    by_status: Dict[str, int]
    by_media_type: Dict[str, int]
    total_lines: int
    translated_lines: int
    usage: LibraryUsage


class LibraryDramaRef(BaseModel):
    id: int
    title_en: Optional[str] = None
    title_zh: Optional[str] = None
    status: Optional[str] = None
    updated_at: Optional[str] = None
    media_type: Optional[str] = None


class LibraryRecentResponse(BaseModel):
    items: List[LibraryDramaRef]


class LibraryCostRow(BaseModel):
    id: int
    title_en: Optional[str] = None
    title_zh: Optional[str] = None
    translation_engine: Optional[str] = None
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    estimated_cost_usd: float
    call_count: int


class LibraryCostResponse(BaseModel):
    items: List[LibraryCostRow]


class LibrarySeries(BaseModel):
    id: int
    name: str
    is_private: bool
    owned_by_me: bool = Field(description="The signed-in viewer may change its sharing: they created "
                                          "it, or they are an admin and it was made at the PC (or "
                                          "they are at the PC).")
    character_count: int
    glossary_term_count: int
    dramas: List[LibraryDramaRef]


class LibrarySeriesResponse(BaseModel):
    items: List[LibrarySeries]


class LibrarySearchHit(BaseModel):
    drama_id: int
    idx: int
    zh: Optional[str] = None
    en: Optional[str] = None
    title_en: Optional[str] = None
    title_zh: Optional[str] = None


class LibrarySearchResponse(BaseModel):
    count: int
    items: List[LibrarySearchHit]


class LibraryHistoryEntry(BaseModel):
    drama_id: int
    line_idx: Optional[int] = None
    percent_complete: Optional[float] = None
    accessed_at: Optional[str] = None
    title_en: Optional[str] = None
    title_zh: Optional[str] = None


class LibraryHistoryResponse(BaseModel):
    items: List[LibraryHistoryEntry]


class LibraryPresetsResponse(BaseModel):
    items: List[LibraryPreset]


class LibraryVoice(BaseModel):
    id: int
    name: str
    language: Optional[str] = None
    clone_engine: Optional[str] = None
    source_drama: Optional[str] = None
    source_speaker: Optional[str] = None
    notes: Optional[str] = None
    ref_text: Optional[str] = None
    created_at: Optional[str] = None
    clip_available: bool
    # The caller can't see the title the voice came from, so source_drama
    # and ref_text are left out (and must not be saved back as blanks).
    source_hidden: bool = False


class LibraryVoiceBankResponse(BaseModel):
    items: List[LibraryVoice]


class LibraryVoiceDetails(BaseModel):
    """Edit a voice bank entry's notes, transcript and language."""
    model_config = ConfigDict(extra="forbid")
    notes: str = Field("", max_length=1000)
    ref_text: str = Field("", max_length=5000)
    language: Optional[str] = Field(None, max_length=10)


class LibraryRename(BaseModel):
    """Rename a preset or voice-bank entry; nothing else changes."""
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)


class MediaAnalysis(BaseModel):
    """Numbers/booleans only; never a path."""
    drama_id: int
    duration_seconds: float
    has_video: bool
    has_audio: bool
    audio_track_count: int
    sample_rate: Optional[int] = None
    # From media_inspect.
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None
    subtitle_tracks: List["MediaSubtitleTrack"] = Field(default_factory=list)
    suggested_pipeline: List[str] = Field(default_factory=list)  # advisory; nothing is applied
    # A media type value (streamer_vod / asmr / audio_drama / video_drama) and
    # a one-line reason, from filename keywords and track shape; applied only
    # when the user picks "Use this content type".
    content_type_guess: Optional[str] = None
    content_type_reason: Optional[str] = None


class MediaSubtitleTrack(BaseModel):
    index: Optional[int] = None
    codec: str
    language: Optional[str] = None
    # Text tracks can be imported as lines; picture tracks (PGS, DVD) can't.
    importable: bool = False


MediaAnalysis.model_rebuild()


class AutofillRequest(BaseModel):
    """Exactly one of url / page_text. No keys: resolved server-side."""
    model_config = ConfigDict(extra="forbid")
    url: Optional[str] = Field(None, max_length=2000)
    page_text: Optional[str] = Field(None, max_length=200000)
    engine: Optional[str] = Field(None, max_length=40)


class AutofillSuggestion(BaseModel):
    drama_id: int
    suggestion: dict[str, str]
    found: bool


class AutofillApply(BaseModel):
    """Whitelisted suggestion fields only; unknown keys are a 422."""
    model_config = ConfigDict(extra="forbid")
    title_en: Optional[str] = Field(None, max_length=300)
    title_zh: Optional[str] = Field(None, max_length=300)
    author: Optional[str] = Field(None, max_length=300)
    studio: Optional[str] = Field(None, max_length=300)
    director: Optional[str] = Field(None, max_length=300)
    voice_actors: Optional[str] = Field(None, max_length=300)
    summary: Optional[str] = Field(None, max_length=5000)
    source_url: Optional[str] = Field(None, max_length=2000)


class MediaExportStarted(BaseModel):
    """Audiobook / burned-in video export job started.
    Poll GET /api/jobs/{job_id}; download via GET /api/artifacts/dramas/{id}/{kind}."""
    job_id: str


class SoftsubVideoRequest(BaseModel):
    """Which subtitles go into the muxed track."""
    model_config = ConfigDict(extra="forbid")
    field: str = Field(default="en", pattern="^(en|zh|bilingual)$")
    include_notes: StrictBool = False
    subtitle_format: str = Field(default="srt", pattern="^(srt|ass)$")


class DubbedVideoRequest(BaseModel):
    """keep_original mixes the original audio in at -20 dB
    instead of replacing it."""
    model_config = ConfigDict(extra="forbid")
    keep_original: StrictBool = False


class MarkExportedResult(BaseModel):
    """The drama's status after "Mark as exported"."""
    drama_id: int
    status: str


# ---------------------------------------------------------------------------
# Workflow progress (GET /api/workflow/dramas/{id}/progress)
# ---------------------------------------------------------------------------
class WorkflowStageState(BaseModel):
    key: str     # source | translate | review | dub | export
    state: str   # done | current | pending | optional | blocked


class WorkflowProgress(BaseModel):
    """Pipeline progress for the React stage bar. `stage_index` is the 7-tab
    scale of `compute_workspace_stage_index` (0-2 source, 3 translate,
    4 review, 6 export; 5/dub is never current). Booleans only, no paths."""
    drama_id: int
    stage_index: int
    stage: str
    line_count: int
    untranslated_count: int
    flagged_count: int
    has_audio: bool
    has_dub_track: bool
    has_narration_track: bool = False
    exported: bool
    stages: List[WorkflowStageState]


# ---------------------------------------------------------------------------
# PC-only delete routes
# ---------------------------------------------------------------------------
class DeleteConfirm(ConfirmRequest):
    """Body of every PC-only delete: `confirm: true` (strict) is the whole
    bar; no typed word."""


class MediaRemoveResult(BaseModel):
    drama_id: int
    removed: bool
    audio_file_removed: bool
    video_file_removed: bool
    has_audio: bool
    has_video: bool


class RawNovelRemoveResult(BaseModel):
    drama_id: int
    removed: bool
    has_raw_novel_context: bool


class PresetDeleteResult(BaseModel):
    preset_id: int
    deleted: bool


class VoiceBankDeleteResult(BaseModel):
    entry_id: int
    deleted: bool


# ---------------------------------------------------------------------------
# Workspace video-URL download (services/url_media_service.py); PC-only.
# The job is read with GET /api/jobs/{job_id}.
# ---------------------------------------------------------------------------
class MediaUrlDownloadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Empty only with keep_lines, which then uses the title's own link.
    url: StrictStr = Field(default="", max_length=2000)
    audio_only: StrictBool
    confirm_replace_audio: StrictBool = False
    # "Download anyway" for one video over 6 h (cap 12 h); never remembered.
    allow_long: StrictBool = False
    # "Only this part": h:mm:ss, either end may be left out (start 0 / to the end).
    part_start: Optional[StrictStr] = Field(default=None, max_length=12)
    part_end: Optional[StrictStr] = Field(default=None, max_length=12)
    exact_cuts: StrictBool = False
    # "Video quality": the tallest picture to keep; left out means the best on offer.
    max_height: Optional[Literal[2160, 1440, 1080, 720, 480, 360]] = None
    # "Download again, keep the lines": the same video at another quality; the
    # job refuses a file whose length doesn't match the title's current media.
    keep_lines: StrictBool = False


class MediaUrlDownloadStarted(BaseModel):
    job_id: str


# ---------------------------------------------------------------------------
# Library: Continue reading shelf, the data-driven
# "All dramas" filter choices, and the PC-only reading-history clear.
# ---------------------------------------------------------------------------
class LibraryContinueEntry(BaseModel):
    drama_id: int
    title_en: Optional[str] = None
    title_zh: Optional[str] = None
    percent_complete: Optional[float] = None
    last_page: Optional[int] = None
    last_accessed_at: Optional[str] = None
    has_cover_art: bool


class LibraryContinueResponse(BaseModel):
    items: List[LibraryContinueEntry]


class LibraryFilterOptions(BaseModel):
    studios: List[str]
    authors: List[str]
    voice_actors: List[str]
    custom_tags: List[str]


# ---------------------------------------------------------------------------
# Workspace preamble: romanize credits and
# cover art. No filename or path is returned.
# ---------------------------------------------------------------------------
class RomanizeCreditsRequest(BaseModel):
    """`engine` defaults to the drama's translation engine."""
    model_config = ConfigDict(extra="forbid")
    engine: Optional[str] = Field(default=None, max_length=40)


class RomanizeCreditsResult(BaseModel):
    drama_id: int
    romanized: Dict[str, str]
    updated: bool


class DramaFolderOpened(BaseModel):
    opened: bool
    # Set only when opened is false because the server has no desktop: the PC owner's own
    # folder to open by hand. The route is local_only, so the path never leaves the PC.
    folder: Optional[str] = None


class DramaSourceLink(BaseModel):
    """PC-only: the link exactly as it was imported or typed (it may carry a
    query or token), else the cleaned one shown everywhere else."""
    url: str


class CoverArtResult(BaseModel):
    drama_id: int
    has_cover_art: bool
    format: str
    width: int
    height: int
    size_bytes: int


class BurnPreviewStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    line_id: int = Field(ge=1)
    pad_seconds: Optional[float] = Field(None, ge=0, le=5)
    preset: Optional[str] = Field(None, max_length=40)
    style: Optional[AssStyleOverrides] = None
    speaker_colors: Optional[Dict[str, str]] = None
    per_speaker_colors: bool = False
    wrap_chars_en: Optional[int] = Field(default=None, ge=0, le=200)
    wrap_chars_source: Optional[int] = Field(default=None, ge=0, le=200)


class BurnPreviewStarted(BaseModel):
    job_id: str
    drama_id: int
    line_id: int
    start: float
    end: float


class BurnPreviewClip(BaseModel):
    line_id: Optional[int] = None
    idx: Optional[int] = None
    start: Optional[float] = None
    end: Optional[float] = None
    preset: Optional[str] = None
    created_at: Optional[str] = None


class BurnPreviewInfo(BaseModel):
    drama_id: int
    has_video: bool
    ffmpeg_available: bool
    presets: List[str]
    max_clip_seconds: float
    max_pad_seconds: float
    clip: Optional[BurnPreviewClip] = None
