import type { TimingCheckStatus, TimingSnapResult, TimingSuggestion } from '../../../../types/timingCheck'

const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? '' : 's'}`

// The one-line outcome of a finished check. A title-level notice means nothing
// was judged, so it is shown instead of a count.
export function checkSummary(status: Pick<TimingCheckStatus, 'last_check'>): string | null {
  const last = status.last_check
  if (!last) return null
  if (last.notice) return last.notice
  return last.flagged === 0 ? 'No line timing disagrees with the audio.' : `${plural(last.flagged, 'line')} flagged for timing.`
}

export function snapSummary(r: TimingSnapResult): string {
  const parts = [`Snapped ${plural(r.snapped, 'line')} to speech.`]
  if (r.stale_ids.length) parts.push(`${plural(r.stale_ids.length, 'line')} changed since the check, so left alone; check timing again.`)
  if (r.snapped) parts.push('Undo from Versions and history.')
  return parts.join(' ')
}

export function suggestionsByLine(list: readonly TimingSuggestion[]): Map<number, TimingSuggestion> {
  return new Map(list.map((s) => [s.line_id, s]))
}
