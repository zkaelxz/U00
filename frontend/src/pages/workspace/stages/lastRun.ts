// Pure helpers for the Last run card (JobPanel.tsx): which finished record a
// stage shows when nothing is running, and the counts it carries.
import { TERMINAL_STATUSES, type JobRecord } from '../../../types/jobs'

const endedAt = (job: JobRecord) => job.finished_at ?? job.updated_at ?? 0

// The newest finished job among the stage's own job ids (the list
// useReattachJob gets, so a stage never shows another stage's run). The ids
// embed the drama id, but a record that names one is checked as well, so a
// stale or hand-made record for another title can't show up here.
export function lastRunFor(jobs: readonly JobRecord[] | null, dramaId: number, ids: readonly string[]): JobRecord | null {
  if (!jobs) return null
  let best: JobRecord | null = null
  for (const job of jobs) {
    if (!TERMINAL_STATUSES.includes(job.status) || !ids.includes(job.job_id)) continue
    if (job.drama_id != null && job.drama_id !== dramaId) continue
    if (!best || endedAt(job) > endedAt(best)) best = job
  }
  return best
}

const count = (v: unknown): number | null =>
  typeof v === 'number' && Number.isFinite(v) ? v : Array.isArray(v) ? v.length : null

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`

// The count keys services/jobs_service.py RESULT_ALLOWED_KEYS can carry, in
// display order; each reads as "12 lines", "3 failed". Bulk library runs
// count dramas, not lines.
const COUNTS: [key: string, text: (n: number) => string][] = [
  ['line_count', (n) => plural(n, 'line')],
  ['lines_replaced', (n) => `${n} replaced`],
  ['split_lines', (n) => `${n} split`],
  ['imported_count', (n) => `${n} imported`],
  ['fixed_count', (n) => `${n} fixed`],
  ['flagged_count', (n) => `${n} flagged`],
  ['flagged', (n) => `${n} flagged`],
  ['skipped_count', (n) => `${n} skipped`],
  ['failed_count', (n) => `${n} failed`],
  ['errors', (n) => `${n} failed`],
]

// "12 lines · 3 failed" from the record's projected result, or '' when it
// carries no counts (an export, say).
export function lastRunCounts(job: Pick<JobRecord, 'result'>): string {
  const result = job.result ?? {}
  const parts: string[] = []
  const bulk = result.bulk
  if (bulk && typeof bulk === 'object') {
    const b = bulk as Record<string, unknown>
    const done = count(b.translated_count)
    const failed = count(b.failed_count)
    if (done !== null) parts.push(plural(done, 'drama'))
    if (failed) parts.push(`${failed} failed`)
    return parts.join(' · ')
  }
  for (const [key, text] of COUNTS) {
    const n = count(result[key])
    if (n === null || (n === 0 && key !== 'line_count')) continue
    parts.push(text(n))
  }
  return parts.join(' · ')
}
