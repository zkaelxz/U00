import type { JobRecord } from '../../../../types/jobs'
import { jobFailed } from '../../../../types/jobs'
import type {
  RetranscribeManyApplyItem,
  RetranscribeManyApplyResult,
  RetranscribeManyFailure,
  RetranscribeManyProposal,
  TranscribeGap,
} from '../../../../types/workspace'
import { COMPARE_MAX_LINES } from './compareTranscriptionLogic'
import { formatTime } from './reviewLogic'
import type { View } from './waveformLogic'

// The server's cap on one run; it still enforces it.
export const RETRANSCRIBE_MAX_LINES = COMPARE_MAX_LINES

// Why "Re-transcribe selected…" can't run with `count` lines ticked, or null.
export function retranscribeLinesProblem(count: number, maxLines = RETRANSCRIBE_MAX_LINES): string | null {
  if (count < 1) return 'Tick at least one line.'
  return count > maxLines
    ? `${count} lines ticked; re-transcribing takes up to ${maxLines} at a time. Untick some and run it in parts.`
    : null
}

// A finished many-line job: the proposals are ready to fetch, or a plain-words reason there are none.
export function retranscribeLinesOutcome(
  job: Pick<JobRecord, 'status' | 'outcome' | 'result'>,
): { kind: 'ready' } | { kind: 'none'; text: string } {
  const reason = job.result?.failed_reason
  if (job.status === 'done' && !jobFailed(job) && !reason) return { kind: 'ready' }
  if (job.outcome === 'cancelled' || job.status === 'cancelled' || reason === 'cancelled') {
    return { kind: 'none', text: 'Cancelled. No line was changed.' }
  }
  if (reason === 'model_download') return { kind: 'none', text: 'The speech model could not be downloaded.' }
  if (reason === 'timeout') {
    return { kind: 'none', text: 'Transcribing took too long and was stopped. The speech model may be stuck.' }
  }
  return { kind: 'none', text: 'Re-transcribing failed. No line was changed.' }
}

const FAILURE_WORDS: Record<string, string> = {
  empty: 'no speech heard',
  audio_slice: 'its audio could not be cut',
  line_gone: 'the line was merged, split or deleted meanwhile',
}

export function failureSummary(failures: RetranscribeManyFailure[]): string | null {
  if (failures.length === 0) return null
  return failures
    .map((f) => `#${f.number} (${FAILURE_WORDS[f.reason] ?? 'could not be re-transcribed'})`)
    .join(', ')
}

export function applyItems(rows: RetranscribeManyProposal[], checked: ReadonlySet<number>): RetranscribeManyApplyItem[] {
  return rows
    .filter((p) => checked.has(p.line_id))
    .map((p) => ({ line_id: p.line_id, expected_zh: p.base_zh, expected_proposed: p.proposed_zh }))
}

export function applyNote(r: RetranscribeManyApplyResult, clearedEnglish: number): string {
  const n = r.applied.length
  const parts = [`Replaced the source text of ${n} line${n === 1 ? '' : 's'}.`]
  if (clearedEnglish > 0) {
    parts.push(
      `Cleared the English on ${clearedEnglish}; Translate will pick ${clearedEnglish === 1 ? 'it' : 'them'} up (${r.untranslated_count} left).`,
    )
  }
  if (r.skipped.length > 0) {
    parts.push(`${r.skipped.length} changed since the run, so left alone.`)
  }
  if (n > 0) parts.push('Undo from Versions and history.')
  return parts.join(' ')
}

// ---- gaps on the waveform ----

export function gapsInView(gaps: TranscribeGap[], view: View): TranscribeGap[] {
  return gaps.filter((g) => g.end > view.start && g.start < view.start + view.span)
}

export function gapLabel(g: Pick<TranscribeGap, 'start' | 'end' | 'seconds' | 'pieces' | 'speech'> & Partial<Pick<TranscribeGap, 'part' | 'parts'>>): string {
  const length = `${Math.round(g.seconds)} s`
  const lines = g.pieces > 1 ? `, ${g.pieces} lines` : ''
  const part = g.parts && g.parts > 1 ? `part ${g.part} of ${g.parts}, ` : ''
  return `${formatTime(g.start)} – ${formatTime(g.end)} (${part}${length}${lines}${g.speech ? ', speech heard' : ''})`
}

// Why a gap's button is off, or null.
export function gapBlockedReason(opts: { jobRunning: boolean; editing: boolean; canTranscribe: boolean }): string | null {
  if (!opts.canTranscribe) return 'Needs this title’s audio or video.'
  if (opts.editing) return 'Save or cancel the open edit first.'
  if (opts.jobRunning) return 'Another job is running on this title. Try again when it finishes.'
  return null
}
