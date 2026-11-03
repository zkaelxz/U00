// Hand-written mirrors of the Review AI extras in api/schemas/review.py (MergeShort*,
// EnCleanup*, Style*, SenseVoice*) and library.py (BurnPreview*). Routes: /api/review-extras/dramas/{id}/...
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

import type { AssStyleOverrides } from './export'
import type { RestructureResult } from './restructure'

export type MergeShortOptions = Schemas['MergeShortOptions']

export type MergeShortPreview = Schemas['MergeShortPreview']

export interface MergeShortApply extends Partial<MergeShortOptions> {
  expected_line_ids: number[]
  expected_groups: number[][]
}

export interface MergeShortResult extends RestructureResult {
  merged_groups: number
}

export type EnCleanupPreview = Schemas['EnCleanupPreview']

export type EnCleanupResult = Schemas['EnCleanupResult']

export type StyleState = Schemas['StyleState']

export type StyleLearnRequest = Schemas['StyleLearnRequest']

export type SenseVoiceStarted = Schemas['SenseVoiceStarted']

export type SenseVoiceTags = Schemas['SenseVoiceTags']

export interface BurnPreviewStart {
  line_id: number
  pad_seconds?: number | null
  preset?: string | null
  style?: AssStyleOverrides
  speaker_colors?: Record<string, string>
  per_speaker_colors?: boolean
  wrap_chars_en?: number
  wrap_chars_source?: number
}

export type BurnPreviewStarted = Schemas['BurnPreviewStarted']

export type BurnPreviewClip = Schemas['BurnPreviewClip']

export type BurnPreviewInfo = Schemas['BurnPreviewInfo']
