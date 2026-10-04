"""api/schemas/characters.py -- Characters, glossary and series-person shapes.
"""

from typing import Annotated, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

__all__ = [
    "CharactersEntry",
    "CharactersUpdateRequest",
    "CharactersSeriesEntry",
    "CharactersCloneEngineItem",
    "CharactersCloneEngines",
    "CharactersVoiceBankEntry",
    "CharactersVoiceBankApply",
    "GlossaryTerm",
    "GlossaryTermUpsert",
    "GlossaryImportRequest",
    "GlossaryImportResult",
    "GlossaryBulkDeleteRequest",
    "GlossaryBulkDeleteResult",
    "GlossaryInstructions",
    "GlossaryInstructionsUpdate",
    "GlossaryCatalogueOption",
    "GlossaryTermPolicyOption",
    "GlossaryWorkflowTier",
    "GlossaryCatalogues",
    "SeriesCharacterDeleteResult",
    "NovelGlossaryRunResult",
    "NovelGlossaryProposal",
    "NovelGlossaryStatus",
    "NovelGlossaryApplyRequest",
    "NovelGlossaryApplyResult",
    "SeriesPersonCreate",
    "SeriesPersonUpdate",
    "CharactersVoiceSuggestion",
    "CharactersVoiceSuggestionRequest",
    "CharactersVoiceSuggestionResult",
    "CharactersRememberRequest",
    "CharactersRememberResult",
    "CharactersRenameRequest",
    "CharactersRenameUndoLine",
    "CharactersRenameUndo",
    "CharactersRenameUndoRequest",
    "CharactersRenameResult",
    "CharactersMergeRequest",
    "CharactersMergeUndo",
    "CharactersMergeUndoRequest",
    "CharactersMergeResult",
    "LinesGlossaryRunResult",
    "GlossaryProposalEdit",
    "GlossaryProposalsApplyRequest",
    "LinesGlossaryApplyRequest",
    "GlossaryRunCancelRequest",
]


class CharactersEntry(BaseModel):
    """One speaker's character/voice settings. No reference-audio
    filename or path -- only the two booleans (D2)."""
    speaker_label: str
    character_name: str
    voice_actor: str = ""
    pronouns: str
    tts_voice: str
    offline_voice: str
    clone_engine: str
    voice_design: str
    has_ref_audio: bool
    ref_text_present: bool
    series_character_id: Optional[int] = None
    series_character_name: str
    line_count: int
    # C07/C04: the linked series character's pronouns (the default when
    # this drama sets none) and up to two short sample source lines.
    series_pronouns: str = ""
    sample_lines: List[str] = Field(default_factory=list)


class CharactersUpdateRequest(BaseModel):
    """speaker_label identifies the speaker; every other field is
    optional -- omitted (or null) leaves the stored value alone, an
    explicit "" clears it (character_name can't be blank)."""
    model_config = {"extra": "forbid"}

    speaker_label: str
    character_name: Optional[str] = None
    voice_actor: Optional[str] = None
    pronouns: Optional[str] = None
    tts_voice: Optional[str] = None
    offline_voice: Optional[str] = None
    clone_engine: Optional[str] = None
    voice_design: Optional[str] = None
    ref_text: Optional[str] = None


class CharactersSeriesEntry(BaseModel):
    id: int
    character_name: str
    aliases: str
    notes: str
    pronouns: str


class CharactersCloneEngineItem(BaseModel):
    id: str
    label: str
    is_default: bool
    language_gated: bool
    local_model: bool


class CharactersCloneEngines(BaseModel):
    source_language: str
    default_engine: str
    engines: List[CharactersCloneEngineItem]


class CharactersVoiceBankEntry(BaseModel):
    """Voice bank picklist entry: metadata only, never the clip file."""
    id: int
    name: str
    clone_engine: str
    voice_design: str
    language: str
    notes: str
    ref_text_present: bool


class CharactersVoiceBankApply(BaseModel):
    speaker_label: str
    voice_bank_id: int = Field(ge=1, le=2147483647)


# --- Glossary, instructions and catalogues (Migration Slice 46) -----------
class GlossaryTerm(BaseModel):
    id: int
    term_original: str
    term_translation: str
    notes: str = ""
    category: Optional[str] = None
    policy: Optional[str] = None
    enforce_exact: bool = False
    aliases: List[str] = Field(default_factory=list)
    banned_translations: List[str] = Field(default_factory=list)


class GlossaryTermUpsert(BaseModel):
    """With `id` the term is updated in place (omitted fields keep their
    stored value); without it the term is keyed on term_original.
    term_original/term_translation are required for a new term (the
    service enforces that)."""
    model_config = {"extra": "forbid"}

    id: Optional[int] = None
    term_original: Optional[str] = None
    term_translation: Optional[str] = None
    notes: Optional[str] = None
    category: Optional[str] = None
    policy: Optional[str] = None
    enforce_exact: Optional[bool] = None
    aliases: Optional[List[str]] = None
    banned_translations: Optional[List[str]] = None


class GlossaryImportRequest(BaseModel):
    """Parity T03: a glossary file's text (CSV, TSV or JSON), pasted or read
    by the browser; filename only hints the format. overwrite_existing
    needs confirm=true."""
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=1_000_000)
    filename: str = Field(default="", max_length=255)
    overwrite_existing: StrictBool = False
    confirm: StrictBool = False


class GlossaryImportResult(BaseModel):
    added: List[str]
    overwritten: List[str]
    skipped_existing: List[str]
    invalid: List[str]
    warnings: List[str]


class GlossaryBulkDeleteRequest(BaseModel):
    """Parity X13: term ids (never positions); needs confirm=true."""
    model_config = ConfigDict(extra="forbid")
    term_ids: List[StrictInt] = Field(min_length=1, max_length=1000)
    confirm: StrictBool = False


class GlossaryBulkDeleteResult(BaseModel):
    deleted: List[int]
    not_found: List[int]


class GlossaryInstructions(BaseModel):
    project_instructions: str
    series_instructions: str


class GlossaryInstructionsUpdate(BaseModel):
    model_config = {"extra": "forbid"}

    text: str


class GlossaryCatalogueOption(BaseModel):
    key: str
    label: str


class GlossaryTermPolicyOption(BaseModel):
    key: str
    label: str
    example: str = ""


class GlossaryWorkflowTier(BaseModel):
    key: str
    label: str
    translation_engine: str
    engine_model: Optional[str] = None
    reflect: bool
    auto_qc: bool


class GlossaryCatalogues(BaseModel):
    style_presets: List[GlossaryCatalogueOption]
    term_categories: List[GlossaryCatalogueOption]
    term_policies: List[GlossaryTermPolicyOption]
    workflow_tiers: List[GlossaryWorkflowTier]


class SeriesCharacterDeleteResult(BaseModel):
    series_id: int
    character_id: int
    deleted: bool


class NovelGlossaryRunResult(BaseModel):
    job_id: str
    engine: str
    paired: bool


class NovelGlossaryProposal(BaseModel):
    term: str
    suggested_translation: str
    category: Optional[str] = None
    policy: Optional[str] = None
    reason: str = ""
    already_in_glossary: bool


class NovelGlossaryStatus(BaseModel):
    """This drama's glossary-from-novel job as held in this app session.
    proposals only once status is "done". No run held: status "idle". Never
    carries a key."""
    job_id: str
    status: str
    progress: Optional[float] = None
    message: str = ""
    proposals: Optional[List[NovelGlossaryProposal]] = None
    # Names this run; the apply sends it back (409 if the run was replaced).
    run_id: Optional[str] = None


class NovelGlossaryApplyRequest(BaseModel):
    """Terms are matched by their text against the finished run's
    proposals, never by position. overwrite_existing needs confirm=true."""
    model_config = ConfigDict(extra="forbid")
    terms: List[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        min_length=1, max_length=1000)
    overwrite_existing: StrictBool = False
    confirm: StrictBool = False


class NovelGlossaryApplyResult(BaseModel):
    added: List[str]
    overwritten: List[str]
    skipped_existing: List[str]
    unknown: List[str]


# Parity X15-X17: add and edit a series' people
# (services/series_people_service.py). Responses reuse CharactersSeriesEntry.
# ---------------------------------------------------------------------------
class SeriesPersonCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    character_name: str = Field(max_length=200)
    pronouns: str = Field("", max_length=40)
    aliases: str = Field("", max_length=1000)
    notes: str = Field("", max_length=2000)


class SeriesPersonUpdate(BaseModel):
    """Omitted (or null) leaves a field alone; "" clears it
    (character_name can't be blank)."""
    model_config = ConfigDict(extra="forbid")
    character_name: Optional[str] = Field(None, max_length=200)
    pronouns: Optional[str] = Field(None, max_length=40)
    aliases: Optional[str] = Field(None, max_length=1000)
    notes: Optional[str] = Field(None, max_length=2000)


# ---------------------------------------------------------------------------
# Characters extras (inventory C02, C08): recurring-voice suggestions and
# "remember as a known series character" (services/characters_service.py).
# ---------------------------------------------------------------------------
class CharactersVoiceSuggestion(BaseModel):
    speaker_label: str
    series_character_id: int
    character_name: str
    similarity: float


class CharactersVoiceSuggestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    speaker_label: str = Field(min_length=1, max_length=100)
    series_character_id: int = Field(ge=1, le=2147483647)


class CharactersVoiceSuggestionResult(BaseModel):
    """character: the updated speaker after an accept (null after a
    reject); suggestions: what is still offered."""
    character: Optional[CharactersEntry] = None
    suggestions: List[CharactersVoiceSuggestion]


class CharactersRememberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    speaker_label: str = Field(min_length=1, max_length=200)


class CharactersRenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    speaker_label: str = Field(min_length=1, max_length=200)
    new_name: str = Field(min_length=1, max_length=200)


class CharactersRenameUndoLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: StrictInt
    speaker: str = Field(max_length=200)
    speaker_manual: StrictBool


class CharactersRenameUndo(BaseModel):
    """What undoes one rename: the lines' previous labels, by line id."""
    model_config = ConfigDict(extra="forbid")
    speaker_label: str = Field(max_length=200)
    previous_label: str = Field(min_length=1, max_length=200)
    previous_character_name: Optional[str] = Field(default=None, max_length=200)
    previous: List[CharactersRenameUndoLine] = Field(max_length=100000)


class CharactersRenameUndoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    undo: CharactersRenameUndo


class CharactersRenameResult(BaseModel):
    characters: List[CharactersEntry]
    renamed: int
    undo: Optional[CharactersRenameUndo] = None


class CharactersMergeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_label: str = Field(min_length=1, max_length=200)
    target_label: str = Field(min_length=1, max_length=200)
    confirm: StrictBool = False

    @model_validator(mode="after")
    def _needs_confirm(self):
        if self.confirm is not True:
            raise ValueError("Merging speakers needs confirm=true.")
        return self


class CharactersMergeUndo(BaseModel):
    """Only an opaque handle: the rows it restores stay on the server."""
    undo_id: str
    expires_in: int


class CharactersMergeUndoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    undo_id: str = Field(min_length=20, max_length=64)


class CharactersMergeResult(BaseModel):
    characters: List[CharactersEntry]
    moved: int
    undo: Optional[CharactersMergeUndo] = None


class CharactersRememberResult(BaseModel):
    character: CharactersEntry
    series_character: CharactersSeriesEntry
    created: bool


# Glossary helpers (parity X10/X28): glossary from the drama's source lines,
# and per-term edits on applying either extraction's proposals.
# ---------------------------------------------------------------------------
class LinesGlossaryRunResult(BaseModel):
    job_id: str
    engine: str
    line_count: int


class GlossaryProposalEdit(BaseModel):
    """The user's edit of one proposal in review. A field left out keeps
    the proposal's value; category/policy null means "none"."""
    model_config = ConfigDict(extra="forbid")
    translation: Optional[Annotated[str, Field(min_length=1, max_length=200)]] = None
    category: Optional[Annotated[str, Field(max_length=50)]] = None
    policy: Optional[Annotated[str, Field(max_length=50)]] = None


class GlossaryProposalsApplyRequest(NovelGlossaryApplyRequest):
    """NovelGlossaryApplyRequest plus optional edits keyed by term text
    (never by position); edits for terms not in `terms` are ignored.
    run_id: the status's run_id the user reviewed; a different held run is
    refused with 409 (optional here for older from-novel callers)."""
    overrides: Dict[Annotated[str, Field(min_length=1, max_length=200)], GlossaryProposalEdit] = Field(
        default_factory=dict, max_length=1000)
    run_id: Optional[Annotated[str, Field(min_length=1, max_length=64)]] = None


class LinesGlossaryApplyRequest(GlossaryProposalsApplyRequest):
    """GlossaryProposalsApplyRequest with run_id required (from-lines)."""
    run_id: Annotated[str, Field(min_length=1, max_length=64)]


class GlossaryRunCancelRequest(BaseModel):
    """The run_id from the extraction's status: only that run is cancelled."""
    model_config = ConfigDict(extra="forbid")
    run_id: Annotated[str, Field(min_length=1, max_length=64)]
