"""api/schemas/reader.py -- Reader and novel shapes: reading views, notes,
vocabulary, wiki and novel files.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool

__all__ = [
    "ReaderPageResponse",
    "NovelAttachTextRequest",
    "NovelAttachResult",
    "NovelAttachFromSourcesRequest",
    "NovelOcrResult",
    "NovelStatus",
    "ReaderOverview",
    "ReaderProgressRequest",
    "ReaderProgress",
    "ReaderNotesRequest",
    "ReaderNotes",
    "ReaderMediaAvailability",
    "ReaderEngineFields",
    "ReaderLookupRequest",
    "ReaderDefinition",
    "ReaderLookupResult",
    "ReaderVocabWord",
    "ReaderVocabList",
    "ReaderRichExportRequest",
    "ReaderRichExportResult",
    "ReaderWhoRequest",
    "ReaderExplainRequest",
    "ReaderRecapRequest",
    "ReaderScopedLlmRequest",
    "ReaderWikiUpdateRequest",
    "ReaderAnswer",
    "ReaderRecap",
    "ReaderRelationshipMap",
    "ReaderWikiEntry",
    "ReaderWikiList",
    "ReaderWikiUpdateResult",
    "ReaderWikiClearRequest",
    "ReaderWikiClearResult",
    "ReaderChatTurn",
    "ReaderAskRequest",
    "NovelFileStatus",
    "NovelFileUploadResult",
    "NovelReferenceRemoveResult",
    "NovelFileTextRequest",
    "ReadingHistoryClearResult",
]


class ReaderPageResponse(BaseModel):
    """One page of a drama's Reader view. `html` is a complete,
    self-contained document (Migration Slice 4) -- render it in a
    sandboxed iframe via `srcDoc`, the same way Streamlit's `st.iframe`
    embeds it today. Definitions baked into `html` are only ever
    whatever's already been looked up and saved for this drama; this
    endpoint never makes a live/paid dictionary call itself."""
    html: str
    page: int
    page_count: int
    total_lines: int


class NovelAttachTextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(max_length=2_000_000)
    mode: str = Field(default="replace", max_length=10)


class NovelAttachResult(BaseModel):
    char_count: int
    # EPUB attach only: the chapters found and the range that was used (1-based, inclusive).
    epub_chapters: Optional[int] = None
    chapter_from: Optional[int] = None
    chapter_to: Optional[int] = None


class NovelAttachFromSourcesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str = Field(default="replace", max_length=10)


class NovelOcrResult(BaseModel):
    job_id: str


class NovelStatus(BaseModel):
    drama_id: int
    has_novel_text: bool
    char_count: int
    chapters: int
    ocr_running: bool


# ---------------------------------------------------------------------------
# Route batch 2B (M4): Reader API over services/reader_service.py
# ---------------------------------------------------------------------------
class ReaderOverview(BaseModel):
    drama_id: int
    length_display: str
    line_count: int
    percent_complete: float
    last_page: int
    last_line_idx: Optional[int] = None


class ReaderProgressRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: int = Field(ge=1)
    chapter_size: int = Field(40, ge=10, le=200)


class ReaderProgress(BaseModel):
    drama_id: int
    last_page: int
    last_line_idx: int
    percent_complete: float


class ReaderNotesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    notes: str = Field(max_length=100_000)


class ReaderNotes(BaseModel):
    drama_id: int
    notes: str


class ReaderMediaAvailability(BaseModel):
    """What the Watch / listen panel can show -- booleans and labels only.
    React plays the files through /api/media and /api/dub."""
    drama_id: int
    original: Optional[str] = None   # "video" | "audio" | None
    dub: bool
    narration: bool
    caption_tracks: List[str]
    captions_overlay: bool


class ReaderEngineFields(BaseModel):
    """Shared by every LLM request. An omitted engine means Claude (the
    Reader tab's default) and counts as paid for the engine check."""
    model_config = ConfigDict(extra="forbid")
    engine: Optional[str] = Field(None, max_length=40)
    model: Optional[str] = Field(None, max_length=100)


class ReaderLookupRequest(ReaderEngineFields):
    page: int = Field(ge=1)
    chapter_size: int = Field(40, ge=10, le=200)
    use_llm: StrictBool = False


class ReaderDefinition(BaseModel):
    reading: Optional[str] = None
    definitions: List[str] = []


class ReaderLookupResult(BaseModel):
    drama_id: int
    page: int
    definitions: Dict[str, ReaderDefinition]
    saved: int


class ReaderVocabWord(BaseModel):
    word: str
    reading: Optional[str] = None
    definitions: List[str] = []
    language: Optional[str] = None
    first_seen_line_idx: Optional[int] = None
    export_rich: bool


class ReaderVocabList(BaseModel):
    drama_id: int
    count: int
    words: List[ReaderVocabWord]


class ReaderRichExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    words: List[str] = Field(min_length=1, max_length=500)
    queued: StrictBool = True


class ReaderRichExportResult(BaseModel):
    drama_id: int
    updated: int
    queued: bool
    rich_count: int


class ReaderWhoRequest(ReaderEngineFields):
    name: str = Field(min_length=1, max_length=200)
    up_to_line_idx: Optional[int] = Field(None, ge=0)


class ReaderExplainRequest(ReaderEngineFields):
    phrase: str = Field(min_length=1, max_length=200)
    up_to_line_idx: Optional[int] = Field(None, ge=0)


class ReaderRecapRequest(ReaderEngineFields):
    page: int = Field(ge=1)
    chapter_size: int = Field(40, ge=10, le=200)


class ReaderScopedLlmRequest(ReaderEngineFields):
    """Relationships and wiki update: None = no spoiler limit."""
    up_to_line_idx: Optional[int] = Field(None, ge=0)


class ReaderWikiUpdateRequest(ReaderScopedLlmRequest):
    """from_line_idx: resume point (the previous call's next_line_idx)."""
    from_line_idx: int = Field(0, ge=0)


class ReaderAnswer(BaseModel):
    drama_id: int
    answer: Optional[str] = None


class ReaderRecap(BaseModel):
    drama_id: int
    summary: Optional[str] = None
    truncated: bool = False   # only the most recent lines before the page were used


class ReaderRelationshipMap(BaseModel):
    drama_id: int
    characters: List[Dict[str, Any]]
    relationships: List[Dict[str, Any]]
    mermaid: str


class ReaderWikiEntry(BaseModel):
    id: Optional[int] = None
    entry_type: Optional[str] = None
    name: Optional[str] = None
    aliases: Optional[Any] = None
    description: Optional[str] = None
    attributes: Dict[str, Any] = {}
    first_seen_line_idx: Optional[int] = None
    known_through_line_idx: Optional[int] = None


class ReaderWikiList(BaseModel):
    drama_id: int
    entry_types: List[str]
    entries: List[ReaderWikiEntry]


class ReaderWikiUpdateResult(BaseModel):
    """One bounded batch. While `remaining` > 0, call again with
    from_line_idx = next_line_idx."""
    drama_id: int
    updated: int
    remaining: int = 0
    next_line_idx: Optional[int] = None


class ReaderWikiClearRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = False


class ReaderWikiClearResult(BaseModel):
    drama_id: int
    cleared: bool


class ReaderChatTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=20_000)


class ReaderAskRequest(ReaderEngineFields):
    question: str = Field(min_length=1, max_length=2000)
    chat_history: List[ReaderChatTurn] = Field(default_factory=list, max_length=40)


# ---------------------------------------------------------------------------
# Novel files (parity audit B1 #3/#4): the English novel translation
# reference and the raw original-language novel. Booleans and counts only;
# no filename or path is ever returned.
# ---------------------------------------------------------------------------
class NovelFileStatus(BaseModel):
    drama_id: int
    present: bool
    size_bytes: int
    char_count: int


class NovelFileUploadResult(NovelFileStatus):
    replaced: bool


class NovelReferenceRemoveResult(BaseModel):
    drama_id: int
    removed: bool
    present: bool


class NovelFileTextRequest(BaseModel):
    """Pasted text for the novel reference or raw novel. The route reads
    the body itself, capped at 32 MB, before this is validated."""
    model_config = ConfigDict(extra="forbid")
    text: str


class ReadingHistoryClearResult(BaseModel):
    cleared: bool
    removed: int
