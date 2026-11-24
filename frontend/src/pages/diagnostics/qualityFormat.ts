import type { BadgeTone } from '../../components/labels'
import type { QualityCheck, QualityClip } from '../../types/diagnostics'

const METRIC_LABEL: Record<string, string> = {
  cer: 'Text accuracy (1 - CER)',
  wer: 'Text accuracy (1 - WER)',
  der: 'Speaker error (lower is better)',
  chrf: 'Translation (chrF)',
}

const num = (n: number | null | undefined) => (n == null ? '-' : n.toFixed(3))

export function metricLabel(c: QualityClip): string {
  return (c.metric && METRIC_LABEL[c.metric]) || c.metric || c.kind
}

export function clipText(c: QualityClip): string {
  if (c.failed) return 'Failed to run'
  const base = c.baseline == null ? 'no baseline' : `baseline ${num(c.baseline)}`
  const runs = c.runs > 1 ? `, median of ${c.runs} runs` : c.fixed_decoding ? ', fixed decoding' : ''
  return `${num(c.score)} (${base}${runs})`
}

export function clipStatus(c: QualityClip): { tone: BadgeTone; label: string } {
  if (c.status === 'pass') return { tone: 'ok', label: 'OK' }
  if (c.status === 'fail') return { tone: 'bad', label: c.failed ? 'Failed' : 'Below baseline' }
  return { tone: 'neutral', label: 'No baseline' }
}

function ago(days: number): string {
  if (days === 0) return 'today'
  return days === 1 ? '1 day ago' : `${days} days ago`
}

/** Whether this result lets a release go ahead, and why not. */
export function gateStatus(q: QualityCheck): { tone: BadgeTone; label: string; reason: string } {
  if (!q.found) return { tone: 'neutral', label: 'No check yet', reason: 'No release check has been run on this PC yet.' }
  if (q.unreadable) return { tone: 'bad', label: 'Unreadable', reason: 'The newest release check file could not be read. Run the check again.' }
  if (!q.passed) {
    return { tone: 'bad', label: 'Not passed', reason: 'A score failed or fell below its baseline, or the run had no baselines. A release waits for a passing run.' }
  }
  if (!q.fresh) {
    return { tone: 'warn', label: 'Too old', reason: `Passed, but more than ${q.max_age_days} days ago. Run it again before a release.` }
  }
  return { tone: 'ok', label: 'Passed', reason: `Good for a release until it is ${q.max_age_days} days old.` }
}

export function qualitySummary(q: QualityCheck): string {
  const gate = gateStatus(q)
  return q.age_days == null ? gate.label : `${gate.label}, ${ago(q.age_days)}`
}

export function ranText(q: QualityCheck): string {
  if (!q.when) return 'When it ran is unknown.'
  const where = q.machine ? ` on ${q.machine}${q.gpu ? ` (${q.gpu})` : ''}` : ''
  return `Last check ${q.when}${where}${q.age_days == null ? '' : `, ${ago(q.age_days)}`}.`
}

export function marginText(q: QualityCheck): string {
  const margin = q.margin ?? 0.02
  return `Red means worse than the baseline by more than ${margin.toFixed(2)}.`
}
