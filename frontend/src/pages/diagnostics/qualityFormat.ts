import type { BadgeTone } from '../../components/labels'
import type { QualityCheck, QualityClip, QualitySeries } from '../../types/diagnostics'

const KIND_LABEL: Record<string, string> = { transcription: 'Transcription', diarization: 'Speakers', translation: 'Translation' }

const METRIC_LABEL: Record<string, string> = {
  cer: 'share of words heard right',
  wer: 'share of words heard right',
  der: 'share of speech given to the wrong person',
  chrf: 'how close the translation is to the reference',
}

const num = (n: number | null | undefined) => (n == null ? '-' : n.toFixed(3))

export const kindLabel = (c: QualityClip) => KIND_LABEL[c.kind] ?? c.kind

export function metricLabel(c: QualityClip): string {
  return (c.metric && METRIC_LABEL[c.metric]) || c.metric || c.kind
}

/** How far a score moved from its baseline; positive is better (a speaker error rate is better when lower). */
export function improvement(c: QualityClip): number | null {
  if (c.score == null || c.baseline == null) return null
  return c.metric === 'der' ? c.baseline - c.score : c.score - c.baseline
}

export function differenceText(c: QualityClip): string {
  if (c.failed) return 'Failed to run'
  const d = improvement(c)
  if (d == null) return 'No baseline'
  const size = Math.abs(d).toFixed(3)
  if (size === '0.000') return 'Same'
  return d > 0 ? `${size} better` : `${size} worse`
}

export function clipStatus(c: QualityClip): { tone: BadgeTone; label: string } {
  if (c.status === 'pass') return { tone: 'ok', label: 'OK' }
  if (c.status === 'fail') return { tone: 'bad', label: c.failed ? 'Failed' : 'Worse' }
  return { tone: 'neutral', label: 'No baseline' }
}

/** The rows worst first, so the one to look at is on top; ties keep the file's order. */
export function sortedClips(clips: QualityClip[]): QualityClip[] {
  const rank = (c: QualityClip) => (c.status === 'fail' ? 0 : c.status === 'none' ? 1 : 2)
  return clips.map((c, i) => ({ c, i })).sort((a, b) => rank(a.c) - rank(b.c) || a.i - b.i).map((x) => x.c)
}

function ago(days: number): string {
  if (days === 0) return 'today'
  return days === 1 ? '1 day ago' : `${days} days ago`
}

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many)

/** The one-line answer: can a release go ahead on this result, and why not. */
export function verdict(q: QualityCheck): { tone: BadgeTone; title: string } {
  if (!q.found) return { tone: 'neutral', title: 'No check yet' }
  if (q.unreadable) return { tone: 'bad', title: 'The newest check file could not be read' }
  const bad = q.clips.filter((c) => c.status === 'fail')
  if (bad.length) {
    const failed = bad.some((c) => c.failed)
    return { tone: 'bad', title: `Not ready for a release: ${bad.length} of ${q.clips.length} checks ${failed ? 'did not pass' : 'got worse'}` }
  }
  if (!q.passed) return { tone: 'bad', title: 'Not ready for a release: nothing to compare with, because no check has a baseline' }
  if (!q.fresh) return { tone: 'warn', title: 'Passed, but too old for a release' }
  return { tone: 'ok', title: `Ready for a release: all ${q.clips.length} ${plural(q.clips.length, 'check is', 'checks are')} OK` }
}

export function verdictDetail(q: QualityCheck): string {
  if (!q.found) return 'No release check has been run on this PC yet.'
  if (q.unreadable) return 'Run the check again.'
  const where = q.machine ? ` on ${q.machine}${q.gpu ? ` (${q.gpu})` : ''}` : ''
  const runs = q.repeats && q.repeats > 1 ? `, middle of ${q.repeats} runs` : ''
  const when = q.age_days == null ? `Checked ${q.when ?? 'at an unknown time'}` : `Checked ${ago(q.age_days)}`
  const left = q.age_days == null ? null : q.max_age_days - q.age_days
  const valid = left != null && left > 0
    ? `A check counts for a release for ${q.max_age_days} days: ${left} left.`
    : `A check counts for a release for ${q.max_age_days} days, and this one is older.`
  return `${when}${where}${runs}. ${valid}`
}

export function marginText(q: QualityCheck): string {
  const margin = q.margin ?? 0.02
  return `Each number comes from the release check on your own test titles. A change of up to ${margin.toFixed(2)} either way is normal and counts as OK.`
}

/** What to do next; null when there is nothing to do. */
export function nextStep(q: QualityCheck): string | null {
  if (!q.found) return 'Run the check on the GPU PC: python -m tools.release_check MANIFEST.json --yes'
  if (q.unreadable) return 'Run the check again on the GPU PC.'
  const bad = q.clips.filter((c) => c.status === 'fail')
  if (bad.length) {
    return `What to do about the ${bad.length === 1 ? 'worse one' : 'worse ones'}. Run the check again. If it is still worse, look at what changed since the last OK check, or set a new baseline on purpose.`
  }
  if (!q.passed) return 'Add a baseline to each check in the manifest, then run the check again.'
  if (!q.fresh) return 'Run the check again before a release.'
  return null
}

export { num }

const LANGUAGE_ORDER = ['zh', 'ja', 'ko', 'en'] as const
const LANGUAGE_NAME: Record<string, string> = { zh: 'Chinese', ja: 'Japanese', ko: 'Korean', en: 'English', other: 'Language not recorded' }

/** Series grouped by language in a fixed order, a language with no series left out. */
export function groupByLanguage(series: QualitySeries[]): Array<{ language: string; name: string; series: QualitySeries[] }> {
  const keys = [...LANGUAGE_ORDER, 'other']
  return keys
    .map((k) => ({ language: k, name: LANGUAGE_NAME[k], series: series.filter((s) => (s.language ?? 'other') === k) }))
    .filter((g) => g.series.length > 0)
}

/** Whether a later score being higher is better; a speaker error rate is the one that is not. */
export const higherIsBetter = (s: QualitySeries) => s.metric !== 'der'

/** "0.642 on 20 Nov, 0.660 on 24 Nov": the same numbers the picture shows, for a screen reader. */
export function trendText(s: QualitySeries): string {
  const scored = s.points.filter((p) => p.score != null)
  if (scored.length === 0) return 'No score recorded yet.'
  return scored.map((p) => `${num(p.score)} on ${p.when.slice(0, 10)}`).join(', ')
}

/** Latest score against the one before it: "better", "worse" or "same", respecting which way is better. */
export function trendDirection(s: QualitySeries): 'better' | 'worse' | 'same' | null {
  const scored = s.points.filter((p) => p.score != null)
  if (scored.length < 2) return null
  const diff = (scored[scored.length - 1].score as number) - (scored[scored.length - 2].score as number)
  if (Math.abs(diff) < 0.0005) return 'same'
  return (diff > 0) === higherIsBetter(s) ? 'better' : 'worse'
}
