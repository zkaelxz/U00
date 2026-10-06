import { COMPARE_MAX_LINES } from './compareTranscriptionLogic'

// Why "Re-time with Qwen3 aligner…" can't run with `count` lines ticked, or null.
export function retimeSelectedProblem(count: number, maxLines = COMPARE_MAX_LINES): string | null {
  if (count < 1) return 'Tick at least one line.'
  return count > maxLines
    ? `${count} lines ticked; re-timing takes up to ${maxLines} at a time. Untick some and run it in parts.`
    : null
}

export function retimeOutcome(job: {
  status: string
  outcome?: string | null
  result?: Record<string, unknown> | null
}): { kind: 'ready' } | { kind: 'none'; text: string } {
  const reason = job.result?.failed_reason
  if (job.status === 'done' && !reason && job.outcome !== 'failed') return { kind: 'ready' }
  if (job.status === 'cancelled' || reason === 'cancelled' || job.outcome === 'cancelled') {
    return { kind: 'none', text: 'Cancelled before any line was re-timed. Nothing was changed.' }
  }
  if (reason === 'model_download') return { kind: 'none', text: 'The aligner model could not be downloaded.' }
  if (reason === 'dependency_missing') {
    return { kind: 'none', text: 'The Qwen3 aligner needs qwen-asr and torch, which are not installed.' }
  }
  return { kind: 'none', text: 'Re-timing failed. Nothing was changed.' }
}

// Signed shift of the start in seconds, for the "moved by" cell.
export function startShift(p: { start: number; new_start: number }): string {
  const d = p.new_start - p.start
  return `${d > 0 ? '+' : d < 0 ? '−' : ''}${Math.abs(d).toFixed(2)} s`
}
