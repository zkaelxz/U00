// Mirrors api/schemas/library.py: ExportReadiness, FlagActionResult, AutoQcFlagResult,
// AssStyleOverrides, AssExportRequest, AssStyleOptions, MediaExportStarted, ArtifactInfo.
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

export type ExportReadiness = Schemas['ExportReadiness']

export type FlagActionResult = Schemas['FlagActionResult']

export type ReadingSpeedMode = 'normal' | 'relaxed' | 'off'

export interface ReadingSpeedModeResult {
  mode: ReadingSpeedMode
}

export type ClearReadingSpeedFlagsResult = Schemas['ClearReadingSpeedFlagsResult']

export type AutoQcFlagResult = Schemas['AutoQcFlagResult']

type SubtitleFormat = 'srt' | 'vtt' | 'lrc'
export type SubtitleField = 'en' | 'zh' | 'bilingual'

export interface SubtitleOptions {
  fmt: SubtitleFormat
  field: SubtitleField
  includeNotes: boolean
  wrapEn?: number
  wrapSource?: number
  tidyTiming?: boolean
}

export type AssStyleOverrides = Schemas['AssStyleOverrides']

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

export type AssStyleOptions = Schemas['AssStyleOptions']

export type MediaExportStarted = Schemas['MediaExportStarted']

export type ArtifactInfo = Schemas['ArtifactInfo']

// Each video export has its own artifact kind, so each section downloads its own file.
export type ExportMediaKind = 'audio' | 'video' | 'softsub_video' | 'dubbed_video'

// Parity E17: which subtitles go into the muxed track.
export type SoftsubVideoRequest = Schemas['SoftsubVideoRequest']

// Parity E19: keep_original mixes the original audio in quietly underneath.
export type DubbedVideoRequest = Schemas['DubbedVideoRequest']

// Parity E22.
export type MarkExportedResult = Schemas['MarkExportedResult']
