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

export interface ReviewJobStarted {
  job_id: string
  drama_id: number
  kind: string
  engine: string
  model: string | null
  line_count: number
}
