// Hand-written mirrors of api/schemas.py (ReviewLines*, ReviewRecords*, Lines*,
// ReviewJob*). Identity is always the permanent line `id`; `idx` is display-only.

export type LineFilter = 'all' | 'flagged' | 'untranslated'

export interface ReviewLine {
  id: number
  idx: number
  start: number
  end: number
  zh: string
  en: string
  speaker: string | null
  speaker_manual: boolean
  sfx: boolean
  flag: string | null
  flag_note: string | null
  dub_filename: string | null
}

export interface ReviewLinesPage {
  lines: ReviewLine[]
  page: number
  page_size: number
  total: number
  flagged_count: number
  untranslated_count: number
}

export interface FindReplaceRequest {
  find: string
  replace: string
  case_sensitive: boolean
  use_regex: boolean
}

export interface ReviewMatch {
  id: number
  idx: number
  old_text: string
  new_text: string
}

export interface LinePatch {
  start?: number
  end?: number
  zh?: string
  en?: string
  speaker?: string
  sfx?: boolean
  // Field -> the old value the client saw; a mismatch is a 409.
  expected?: Record<string, unknown>
}

export interface ApplyResult {
  applied: number
  stale: number
  applied_ids: number[]
  stale_ids: number[]
}

export interface LineNoteCreate {
  line_id: number
  term: string
  note_type: string
  note: string
}

export interface ReviewNote {
  id: number
  drama_id: number
  line_id: number | null
  line_idx: number | null
  term: string | null
  note_type: string | null
  note: string | null
  created_at: string | null
}

export interface NoteDeleteResult {
  deleted: boolean
  note_id: number
}

export interface HistoryItem {
  id: number
  drama_id: number
  label: string | null
  created_at: string | null
}

export interface VersionItem {
  id: number
  drama_id: number
  label: string | null
  engine: string
  model: string
  is_active: boolean
  created_at: string | null
}

export interface TmSuggestion {
  line_id: number | null
  line_idx: number
  zh: string
  en: string
  suggestion: string
  similarity: number
  exact: boolean
  entry_id: number
}

export type ReviewJobKind = 'consistency' | 'emotion' | 'notes' | 'flag' | 'fix-flagged'

// ReviewJobStart / FixFlaggedJobStart: every field optional (server default).
export interface ReviewJobBody {
  engine?: string
  model?: string
  job_cost_cap_usd?: number
}

export interface ReviewJobStarted {
  job_id: string
  drama_id: number
  kind: string
  engine: string
  model: string | null
  line_count: number
}

export interface LineImprovement {
  line_id: number
  current_en: string
  suggestion: string
  changed: boolean
  engine: string
  model: string | null
}

export interface LineExplanation {
  line_id: number
  explanation: string
  engine: string
  model: string | null
}

// Review parity R17/R18: other translations and a source breakdown (read-only).
export interface LineAlternative {
  translation: string
  approach: string
  tradeoff: string
}

export interface LineAlternatives {
  line_id: number
  current_en: string
  alternatives: LineAlternative[]
  engine: string
  model: string | null
}

export interface LineGrammarPart {
  word: string
  reading: string
  meaning: string
  function: string
}

export interface LineGrammar {
  line_id: number
  zh: string
  parts: LineGrammarPart[]
  engine: string
  model: string | null
}

// Review parity R28: auto-shorten lines too long for their time slot.
export interface ShortenResult {
  shortened: number
  unchanged: number
  stale: number
  remaining: number
  snapshot_saved: boolean
  lines: { id: number; idx: number; before: string; after: string }[]
}

// Review parity R08: the nearest flagged line across pages (all null: none).
export interface FlaggedPosition {
  line_id: number | null
  idx: number | null
  page: number | null
  page_all: number | null
}

// ---- stored AI results and checks (ReviewRecords*, ReviewLines*) ----

// Not tied to one line: a source term translated more than one way.
export interface ConsistencyIssue {
  id: number
  term: string
  variants: string[]
  note: string
  created_at: string | null
}

// line_idx is the line's CURRENT position (the server joins on line id); the
// route returns no line id.
export interface EmotionTag {
  line_idx: number
  emotion: string
  intensity: number | null
  note: string
}

export interface EmotionSummary {
  drama_id: number
  total: number
  by_emotion: Record<string, number>
  high_risk: number
  lines: EmotionTag[]
}

export interface Tendencies {
  drama_id: number
  scope: string
  tendencies: { total: number; shortened: number; expanded: number; rephrased: number; avg_word_delta: number }
  profile: {
    summary: string
    confidence: unknown
    preferences: string[]
    sample_count: number
    updated_at: string | null
  } | null
}

export interface VersionDiff {
  idx: number
  zh: string
  left_en: string
  right_en: string
}

export interface VersionCompare {
  drama_id: number
  left: { id: number; label: string | null }
  right: { id: number; label: string | null }
  left_line_count: number
  diff_count: number
  diffs: VersionDiff[]
}

// Which fields are set depends on the list the entry is in.
export interface CoverageEntry {
  idx?: number | null
  id?: number | null
  start?: number | null
  end?: number | null
  duration?: number | null
  zh?: string | null
  char_count?: number | null
  note?: string | null
  after_idx?: number | null
  before_idx?: number | null
  after_id?: number | null
  before_id?: number | null
  gap_start?: number | null
  gap_end?: number | null
  gap_seconds?: number | null
}

export interface Coverage {
  long_lines: CoverageEntry[]
  large_gaps: CoverageEntry[]
  blank_zh: CoverageEntry[]
  blank_en: CoverageEntry[]
}

export interface PacingFlag {
  id: number | null
  idx: number
  issue: string
  detail: string | null
}

export interface Pacing {
  flags: PacingFlag[]
  count: number
}

// debug_view.explain_line; list sections have variable row shapes.
export interface LineProvenance {
  line_id: number
  line_idx: number
  zh: string
  en: string
  speaker: string | null
  speaker_manual: boolean
  flag: string | null
  flag_reason: string | null
  flag_note: string | null
  translation_notes: unknown[]
  emotion: unknown
  edit_samples: unknown[]
  consistency_issues: unknown[]
  glossary_matches: unknown[]
  glossary_matches_note: string | null
  context_window_used: unknown
  context_window_note: string | null
  current_neighbors_before: unknown[]
  current_neighbors_after: unknown[]
  engine: string | null
  model: string | null
  engine_source: string | null
  prompt_version_note: string | null
}

export interface LineOriginalText {
  line_id: number
  idx: number
  current_zh: string
  has_raw_transcript: boolean
  original_text: string | null
  differs: boolean
}

// Review parity R39: POST /api/review/dramas/{id}/versions/{vid}/activate
export interface VersionActivateResult {
  drama_id: number
  version_id: number
  label: string
  activated: boolean
  lines_changed: number
  // Ids of lines edited after the server read them; they kept their English.
  conflicts: number[]
}

// Review parity R10: POST /api/lines/dramas/{id}/lines/{lid}/retry-blocked
export interface BlockedRetryResult {
  drama_id: number
  line_id: number
  engine: string
  model: string | null
  retried: boolean
  blocked: boolean
  reason: string | null
  line: ReviewLine
}
