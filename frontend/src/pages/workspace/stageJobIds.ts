import type { MediaKind } from '../../types/export'
import type { ReviewJobKind } from '../../types/review'

// The fixed per-drama job ids each stage starts (the prefixes are in
// background_jobs.DRAMA_JOB_PREFIXES), for useReattachJob: a stage revisited
// while one of these runs picks it up again. Source's list is sourceJobIds.

// A bulk translate batch waits on the provider, often for hours; it is listed
// (and can be cancelled) under Bulk batches on the Translate stage.
export const isBulkJobId = (id: string | null) => !!id && id.startsWith('bulk_')

export const translateJobIds = (dramaId: number) => [`translate_${dramaId}`, `bulk_translate_${dramaId}`]

export const dubJobIds = (dramaId: number) => [`dub_${dramaId}`, `narration_${dramaId}`]

// services/review_jobs_service.py _KINDS (fix-flagged runs as fixflag_). Bulk
// review batches (bulk_<kind>_<id>) are left out: they never lock the checks,
// and the Review stage lists them under its own Bulk batches.
const REVIEW_PREFIXES: Record<ReviewJobKind, string> = {
  consistency: 'consistency_',
  emotion: 'emotion_',
  notes: 'notes_',
  flag: 'flag_',
  'fix-flagged': 'fixflag_',
}
export const reviewJobIds = (dramaId: number) => Object.values(REVIEW_PREFIXES).map((p) => `${p}${dramaId}`)
// The check a review job id was started for, or null for a bulk batch or another stage's job.
export function reviewKindOf(jobId: string): ReviewJobKind | null {
  const hit = (Object.entries(REVIEW_PREFIXES) as [ReviewJobKind, string][]).find(([, p]) => jobId.startsWith(p))
  return hit ? hit[0] : null
}

// services/media_export_service.py: one job per artifact kind.
const MEDIA_PREFIXES: Record<MediaKind, string> = {
  audio: 'audiobook_',
  video: 'burned_video_',
  softsub_video: 'softsub_video_',
  dubbed_video: 'dubbed_video_',
}
export const mediaExportJobId = (dramaId: number, kind: MediaKind) => `${MEDIA_PREFIXES[kind]}${dramaId}`

export const voiceCloneJobId = (dramaId: number) => `voiceref_${dramaId}`
// services/restructure_service.py: the rules re-segment and the AI apply share
// resegment_; the AI preview (nothing written) is resegpreview_.
export const resegmentJobIds = (dramaId: number) => [`resegment_${dramaId}`, `resegpreview_${dramaId}`]
export const isResegmentPreviewJob = (id: string) => id.startsWith('resegpreview_')
export const burnPreviewJobId = (dramaId: number) => `burnpreview_${dramaId}`
export const senseVoiceJobId = (dramaId: number) => `sensevoice_${dramaId}`
// The align-to-audio re-split (services/restructure_service.py).
export const resplitJobId = (dramaId: number) => `resplit_${dramaId}`
