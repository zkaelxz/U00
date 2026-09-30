import type { MediaKind } from '../../types/export'
import type { ReviewJobKind } from '../../types/review'

// The fixed per-drama job ids each stage starts (the prefixes are in
// background_jobs.DRAMA_JOB_PREFIXES), for useReattachJob: a stage revisited
// while one of these runs picks it up again. Source's list is sourceJobIds.

// A bulk batch waits on the provider, often for hours; it is listed (and can
// be cancelled) under Bulk batches on the Translate stage.
export const isBulkJobId = (id: string | null) => !!id && id.startsWith('bulk_')

export const translateJobIds = (dramaId: number) => [`translate_${dramaId}`, `bulk_translate_${dramaId}`]

export const dubJobIds = (dramaId: number) => [`dub_${dramaId}`, `narration_${dramaId}`]

// services/review_jobs_service.py _KINDS (fix-flagged runs as fixflag_) and
// the bulk variants (bulk_<kind>_<id>; fix-flagged has none).
const REVIEW_PREFIXES: Record<ReviewJobKind, string> = {
  consistency: 'consistency_',
  emotion: 'emotion_',
  notes: 'notes_',
  flag: 'flag_',
  'fix-flagged': 'fixflag_',
}
export const reviewJobIds = (dramaId: number) => [
  ...Object.values(REVIEW_PREFIXES).map((p) => `${p}${dramaId}`),
  ...['consistency', 'emotion', 'notes', 'flag'].map((k) => `bulk_${k}_${dramaId}`),
]

// services/media_export_service.py: one job per artifact kind.
const MEDIA_PREFIXES: Record<MediaKind, string> = {
  audio: 'audiobook_',
  video: 'burned_video_',
  softsub_video: 'softsub_video_',
  dubbed_video: 'dubbed_video_',
}
export const mediaExportJobId = (dramaId: number, kind: MediaKind) => `${MEDIA_PREFIXES[kind]}${dramaId}`

export const voiceCloneJobId = (dramaId: number) => `voiceref_${dramaId}`
export const resegmentJobId = (dramaId: number) => `resegment_${dramaId}`
export const burnPreviewJobId = (dramaId: number) => `burnpreview_${dramaId}`
export const senseVoiceJobId = (dramaId: number) => `sensevoice_${dramaId}`
