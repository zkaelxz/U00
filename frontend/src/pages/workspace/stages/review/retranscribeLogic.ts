import type { JobRecord } from '../../../../types/jobs'
import { jobFailed } from '../../../../types/jobs'
import type { TranscribeConfig } from '../../../../types/workspace'

// Re-transcribing needs the drama's own audio (novel narration has none).
export function canRetranscribe(
  cfg: Pick<TranscribeConfig, 'has_audio_pipeline' | 'audio_available'> | null,
): boolean {
  return !!cfg && cfg.has_audio_pipeline && cfg.audio_available
}

// What a finished re-transcribe job did to the line, in plain words. The job
// result's line_count is 1 when the text was replaced, 0 when Whisper heard
// the same text; a failed/cancelled job keeps the line as it was.
export function retranscribeDoneText(job: Pick<JobRecord, 'status' | 'outcome' | 'result'>): {
  text: string
  ok: boolean
  changed: boolean
} {
  if (jobFailed(job) || job.status !== 'done') {
    const reason = job.result?.failed_reason
    if (job.outcome === 'cancelled' || reason === 'cancelled') {
      return { text: 'Cancelled. The line was not changed.', ok: false, changed: false }
    }
    if (reason === 'empty') {
      return { text: "No speech found in this line's timing window. The line was not changed.", ok: false, changed: false }
    }
    if (reason === 'line_changed') {
      return { text: 'The line was edited while this ran, so your edit was kept.', ok: false, changed: false }
    }
    if (reason === 'line_gone') {
      return { text: 'The line was merged, split or deleted while this ran. Nothing was changed.', ok: false, changed: false }
    }
    if (reason === 'model_download') {
      return { text: 'The speech model could not be downloaded. The line was not changed.', ok: false, changed: false }
    }
    return { text: 'Re-transcribing failed. The line was not changed.', ok: false, changed: false }
  }
  if (job.result?.line_count === 0) {
    return { text: 'Whisper heard the same text. Nothing changed.', ok: true, changed: false }
  }
  return { text: "Replaced this line's source text.", ok: true, changed: true }
}
