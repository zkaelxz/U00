import type { JobRecord } from '../../../../types/jobs'
import { jobFailed } from '../../../../types/jobs'
import type { TranscribeConfig } from '../../../../types/workspace'

// Re-transcribing needs the drama's own audio (novel narration has none).
export function canRetranscribe(
  cfg: Pick<TranscribeConfig, 'has_audio_pipeline' | 'audio_available'> | null,
): boolean {
  return !!cfg && cfg.has_audio_pipeline && cfg.audio_available
}

export const NO_AUDIO_MESSAGE = 'Needs this drama’s audio or video.'

// The sheet's "Re-transcribe…" item: same availability as the editor's button,
// plus the job guard the sheet's other source-changing items use.
export function retranscribeMenuState(canRun: boolean, jobMessage: string | null): { disabled: boolean; reason: string | null } {
  const reason = jobMessage ?? (canRun ? null : NO_AUDIO_MESSAGE)
  return { disabled: reason !== null, reason }
}

// The job id is per drama, so a record can belong to another line's run. The
// job's result carries only line_id (never line text); a record that names a
// different line is not ours. No line_id yet counts as ours: this editor
// started the run it polls.
export function jobIsForLine(job: Pick<JobRecord, 'result'> | null, lineId: number): boolean {
  const id = job?.result?.line_id
  return typeof id !== 'number' || id === lineId
}

// A finished re-transcribe job: either the proposal is ready to fetch (GET
// .../retranscribe), or a plain-words reason why there is none.
type RetranscribeOutcome = { kind: 'ready' } | { kind: 'none'; text: string }

export function retranscribeOutcome(job: Pick<JobRecord, 'status' | 'outcome' | 'result'>): RetranscribeOutcome {
  const reason = job.result?.failed_reason
  if (job.status === 'done' && !jobFailed(job) && !reason) return { kind: 'ready' }
  if (job.outcome === 'cancelled' || job.status === 'cancelled' || reason === 'cancelled') {
    return { kind: 'none', text: 'Cancelled. The line was not changed.' }
  }
  if (reason === 'empty') return { kind: 'none', text: "No speech found in this line's timing window." }
  if (reason === 'line_gone') {
    return { kind: 'none', text: 'The line was merged, split or deleted while this ran.' }
  }
  if (reason === 'model_download') return { kind: 'none', text: 'The speech model could not be downloaded.' }
  if (reason === 'timeout') {
    return { kind: 'none', text: 'Transcribing this line took too long and was stopped. The speech model may be stuck.' }
  }
  if (reason === 'audio_slice') return { kind: 'none', text: "This line's audio could not be cut." }
  return { kind: 'none', text: 'Re-transcribing failed. The line was not changed.' }
}
