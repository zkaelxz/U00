// Plain-English text and list helpers for the Translate stage's bulk
// batches panel (GET/POST /api/translate-run/dramas/{id}/bulk...).

import { safeDetail } from '../../../components/errorMessages'
import { words } from '../../../components/labels'
import type { BulkJobEntry } from '../../../types/translateStage'

const STATUS: Record<string, string> = {
  submitting: 'Sending to the provider',
  submitted: 'Waiting for the provider',
  scheduled: 'Scheduled for off-peak',
  running: 'Being applied',
  applied: 'Done',
  cancelled: 'Cancelled',
  failed: 'Failed',
  auth_error: 'Key problem: check Settings',
}

export function statusLabel(status: string): string {
  return STATUS[status] ?? status
}

/** Badge class: "ok" when applied, "bad" when it failed, "" otherwise. */
export function statusTone(status: string): '' | 'ok' | 'bad' {
  if (status === 'applied') return 'ok'
  return status === 'failed' || status === 'auth_error' ? 'bad' : ''
}

const KIND: Record<string, string> = {
  translate: 'Translation',
  flag: 'QA flags',
  consistency: 'Consistency check',
  emotion: 'Emotion tags',
  translation_notes: 'Translator notes',
  reflect: 'Reflect',
}

export function kindLabel(job: Pick<BulkJobEntry, 'kind' | 'stage'>): string {
  const k = KIND[job.kind] ?? job.kind
  return job.stage ? `${k} (${job.stage})` : k
}

const RESUME: Record<string, string> = {
  polling: 'now being checked',
  running: 'already being checked',
  needs_key: 'needs an API key in Settings',
}

/** One line after "Check pending batches": what happened to each batch. */
export function resumeText(jobs: { bulk_job_id: number; state: string }[]): string {
  if (jobs.length === 0) return 'No pending batches to check.'
  return jobs.map((j) => `#${j.bulk_job_id} ${RESUME[j.state] ?? j.state}`).join(' · ')
}

/** Pending first (as the server orders them, newest first), then the rest. */
export function splitJobs(jobs: BulkJobEntry[]): { pending: BulkJobEntry[]; finished: BulkJobEntry[] } {
  return { pending: jobs.filter((j) => j.pending), finished: jobs.filter((j) => !j.pending) }
}

/** Swaps in the updated entry a cancel returned; leaves the rest alone. */
export function replaceJob(jobs: BulkJobEntry[], updated: BulkJobEntry): BulkJobEntry[] {
  return jobs.map((j) => (j.bulk_job_id === updated.bulk_job_id ? updated : j))
}

/** Non-zero numeric counts from a finished batch's result summary,
 * e.g. "applied 120 · missing 2". Other shapes are ignored. */
export function summaryText(summary: Record<string, unknown> | null): string {
  if (!summary) return ''
  return Object.entries(summary)
    .filter(([, v]) => typeof v === 'number' && v !== 0)
    .map(([k, v]) => `${words(k)} ${v}`)
    .join(' · ')
}

/** Server text shown to people: kept only when it looks safe (no paths or
 * keys); a trailing "(provider detail)" is dropped first if that is the
 * unsafe or overlong part. */
export function plainServerText(text: string | null, fallback: string): string {
  if (!text) return fallback
  const whole = safeDetail(text)
  if (whole) return whole
  const trimmed = safeDetail(text.replace(/\s*\([^()]*\)\s*$/, ''))
  return trimmed ?? fallback
}

/** "2026-09-29T10:15:00" -> "2026-09-29 10:15"; unknown shapes pass through. */
export function shortTime(iso: string | null): string {
  if (!iso) return ''
  const m = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/.exec(iso)
  return m ? `${m[1]} ${m[2]}` : iso
}
