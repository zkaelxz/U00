// Mirrors api/schemas/subtitle_import.py.

export type SubtitleImportMode = 'source' | 'translation'

export interface SubtitleProblem {
  code: string
  severity: 'warning' | 'error'
  message: string
  count: number
}

export interface SubtitleSampleCue {
  start: number
  end: number
  text: string
}

export interface SubtitleImportPreview {
  format: string
  encoding: string
  encoding_guessed: boolean
  cue_count: number
  duration_seconds: number
  detected_language: string | null
  bilingual_suspected: boolean
  problems: SubtitleProblem[]
  blocking: boolean
  sample: SubtitleSampleCue[]
  mode: SubtitleImportMode
  existing_line_count: number
  replaces_lines: number
  matched_lines: number
  unmatched_cues: number
  overwrites: number
  unsplit_cues: number
  blocked_reason: string | null
}

export interface SubtitleImportResult {
  mode: SubtitleImportMode
  format: string
  encoding: string
  lines_written: number
  line_ids: number[]
  replaced_lines: number
  matched_lines: number
  unmatched_cues: number
  undo: { history_id: number; lines_fingerprint: string } | null
}

export interface SubtitleImportOptions {
  mode: SubtitleImportMode
  encoding: string
  splitBilingual: boolean
  translationFirst: boolean
}

export interface SidecarCandidate {
  name: string
  format: string
  language_token: string | null
  language: string | null
  exact: boolean
}

export interface SidecarMatchResult {
  candidates: SidecarCandidate[]
  ambiguous: boolean
}
