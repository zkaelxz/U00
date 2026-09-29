// Hand-written mirrors of api/schemas.py's Review AI extras (MergeShort*,
// Style*, SenseVoice*, BurnPreview*). Routes: /api/review-extras/dramas/{id}/...
import type { RestructureResult } from './restructure'

export interface MergeShortOptions {
  min_duration: number
  max_gap: number
  max_chars: number
}

export interface MergeShortGroup {
  // The head line (kept); merged_line_ids are folded into it.
  line_id: number
  idx: number
  merged_line_ids: number[]
  start: number
  end: number
  zh: string
  en: string
}

export interface MergeShortPreview {
  drama_id: number
  options: MergeShortOptions
  source_line_ids: number[]
  line_count_before: number
  line_count_after: number
  // Each group: the ids of 2+ lines that become one (first id kept).
  groups: number[][]
  merges: MergeShortGroup[]
}

export interface MergeShortApply extends Partial<MergeShortOptions> {
  expected_line_ids: number[]
  expected_groups: number[][]
}

export interface MergeShortResult extends RestructureResult {
  merged_groups: number
}

export interface StyleProfile {
  summary: string
  confidence: string | null
  preferences: string[]
  sample_count: number
  updated_at: string | null
  // false: paused, left out of every translation until turned back on.
  applied: boolean
}

// An earlier profile a learn or reset replaced (newest first, at most 5).
export interface StyleHistoryEntry {
  summary: string
  preference_count: number
  updated_at: string | null
}

export interface StyleState {
  drama_id: number
  scope: 'series' | 'global' | string
  edit_count: number
  drama_edit_count: number
  min_samples: number
  profile: StyleProfile | null
  history: StyleHistoryEntry[]
  message: string | null
}

export interface StyleLearnRequest {
  engine?: string | null
  model?: string | null
}

export interface SenseVoiceStarted {
  job_id: string
  drama_id: number
  line_count: number
}

export interface SenseVoiceRow {
  line_id: number | null
  idx: number
  text: string
  text_emotion: string
  audio_emotion: string
  audio_events: string
  disagree: boolean
}

export interface SenseVoiceTags {
  drama_id: number
  installed: boolean
  has_audio: boolean
  license_note: string
  tagged: number
  disagree: number
  rows: SenseVoiceRow[]
}

export interface BurnPreviewStart {
  line_id: number
  pad_seconds?: number | null
  preset?: string | null
}

export interface BurnPreviewStarted {
  job_id: string
  drama_id: number
  line_id: number
  start: number
  end: number
}

export interface BurnPreviewClip {
  line_id: number | null
  idx: number | null
  start: number | null
  end: number | null
  preset: string | null
  created_at: string | null
}

export interface BurnPreviewInfo {
  drama_id: number
  has_video: boolean
  ffmpeg_available: boolean
  presets: string[]
  max_clip_seconds: number
  max_pad_seconds: number
  clip: BurnPreviewClip | null
}
