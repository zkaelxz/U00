// Mirrors api/schemas/subtitle_import.py.
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

export type SubtitleImportMode = 'source' | 'translation'

interface SubtitleProblem {
  code: string
  severity: 'warning' | 'error'
  message: string
  count: number
}

interface SubtitleSampleCue {
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

export type SubtitleImportResult = Schemas['SubtitleImportResult']

export interface SubtitleImportOptions {
  mode: SubtitleImportMode
  encoding: string
  splitBilingual: boolean
  translationFirst: boolean
}
