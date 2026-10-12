import type { RetryFailedBody } from '../../../types/translateStage'
import { buildRunBody, lineRanges, validateRun, type RunForm } from '../translateForm'

/** The form's options for a retry. Reflect and bulk are left out: a retry is a normal run. */
export function buildRetryBody(f: RunForm): RetryFailedBody {
  const { force_retranslate: _force, bulk: _bulk, reflect: _reflect, ...options } = buildRunBody(f)
  return options
}

export function retryBlocker(f: RunForm, defaultEngine: string): string | null {
  return validateRun({ ...f, bulk: false, reflect: false }, defaultEngine)
}

/** Failed lines split by what they hold now. A server that does not say (both
 * empty) leaves them all "untranslated", as the notice always read. */
export function splitFailed(all: number[], empty?: number[], kept?: number[]) {
  const e = empty ?? []
  const k = kept ?? []
  return e.length + k.length === 0 ? { empty: all, kept: [] as number[] } : { empty: e, kept: k }
}

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many)

/** "lines 1-3, 9 are still untranslated; line 5 kept its earlier translation" */
export function failedLinesText(empty: number[], kept: number[]): string {
  const parts: string[] = []
  if (empty.length) parts.push(`${plural(empty.length, 'line', 'lines')} ${lineRanges(empty)} ${plural(empty.length, 'is', 'are')} still untranslated`)
  if (kept.length) parts.push(`${plural(kept.length, 'line', 'lines')} ${lineRanges(kept)} kept ${plural(kept.length, 'its', 'their')} earlier translation`)
  return parts.join('; ')
}

export function retryLabel(n: number): string {
  return n === 1 ? 'Retry this line' : `Retry these ${n} lines`
}
