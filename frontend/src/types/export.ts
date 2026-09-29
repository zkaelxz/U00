// Mirrors api/schemas.py: ExportReadiness, FlagActionResult, AutoQcFlagResult,
// AssStyleOverrides, AssExportRequest, AssStyleOptions, MediaExportStarted, ArtifactInfo.

export interface ExportReadiness {
  drama_id: number
  total_lines: number
  zh_filled: number
  en_filled: number
  fully_translated: boolean
  test_mode_output: boolean
  overlap_count: number
  auto_qc_issue_count: number
  dense_line_count: number
}

export interface FlagActionResult {
  flagged_count: number
}

export interface AutoQcFlagResult {
  flagged: number
  cleared: number
  already_flagged: number
  checked: number
}

export type SubtitleFormat = 'srt' | 'vtt'
export type SubtitleField = 'en' | 'zh' | 'bilingual'

export interface SubtitleOptions {
  fmt: SubtitleFormat
  field: SubtitleField
  includeNotes: boolean
  wrapEn?: number
  wrapSource?: number
}

export interface AssStyleOverrides {
  font?: string
  size?: number
  bold?: boolean
  italic?: boolean
  primary?: string
  outline?: string
  outline_width?: number
  shadow?: number
  alignment?: string
  sfx_alignment?: string
  notes_alignment?: string
}

export interface AssExportRequest {
  field: SubtitleField
  style?: AssStyleOverrides
  preset: string
  speaker_colors?: Record<string, string>
  per_speaker_colors: boolean
  include_notes: boolean
  notes_as_separate_line: boolean
  wrap_chars_en?: number
  wrap_chars_source?: number
}

export interface AssStyleOptions {
  presets: Record<string, Record<string, unknown>>
  default_preset: string
  fonts: string[]
  custom_font_allowed: boolean
  alignments: Record<string, number>
  size_range: number[]
  outline_width_range: number[]
  shadow_range: number[]
}

export interface MediaExportStarted {
  job_id: string
}

export interface ArtifactInfo {
  name: string
  size: number
  kind: string
}

export type MediaKind = 'audio' | 'video'

// Parity E17: which subtitles go into the muxed track.
export interface SoftsubVideoRequest {
  field: 'en' | 'zh' | 'bilingual'
  include_notes?: boolean
}

// Parity E19: keep_original mixes the original audio in quietly underneath.
export interface DubbedVideoRequest {
  keep_original: boolean
}

// Parity E22.
export interface MarkExportedResult {
  drama_id: number
  status: string
}
