import type { JobRecord } from '../../../../types/jobs'
import { jobFailed } from '../../../../types/jobs'
import type { TranscribeConfig } from '../../../../types/workspace'

// Re-transcribing needs the drama's own audio (novel narration has none).
export function canRetranscribe(
  cfg: Pick<TranscribeConfig, 'has_audio_pipeline' | 'audio_available'> | null,
): boolean {
  return !!cfg && cfg.has_audio_pipeline && cfg.audio_available
}

// A finished re-transcribe job: either a proposal the user can "Use this" or
// "Discard" (nothing has been written yet), or a plain-words reason why there
// is none. `same` means Whisper heard the text the line already has.
export type RetranscribeOutcome =
  | { kind: 'proposal'; proposed: string; base: string; same: boolean }
  | { kind: 'none'; text: string }

export function retranscribeOutcome(job: Pick<JobRecord, 'status' | 'outcome' | 'result'>): RetranscribeOutcome {
  const r = job.result ?? {}
  const proposed = typeof r.proposed_zh === 'string' ? r.proposed_zh : ''
  const base = typeof r.base_zh === 'string' ? r.base_zh : ''
  if (job.status === 'done' && !jobFailed(job) && proposed) {
    return { kind: 'proposal', proposed, base, same: proposed === base }
  }
  const reason = r.failed_reason
  if (job.outcome === 'cancelled' || job.status === 'cancelled' || reason === 'cancelled') {
    return { kind: 'none', text: 'Cancelled. The line was not changed.' }
  }
  if (reason === 'empty') return { kind: 'none', text: "No speech found in this line's timing window." }
  if (reason === 'line_gone') {
    return { kind: 'none', text: 'The line was merged, split or deleted while this ran.' }
  }
  if (reason === 'model_download') return { kind: 'none', text: 'The speech model could not be downloaded.' }
  return { kind: 'none', text: 'Re-transcribing failed. The line was not changed.' }
}
