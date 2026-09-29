import { describe, expect, it } from 'vitest'

import type { BulkJobEntry } from '../../../types/translateStage'
import {
  kindLabel,
  plainServerText,
  replaceJob,
  resumeText,
  shortTime,
  splitJobs,
  statusLabel,
  statusTone,
  summaryText,
} from './bulkBatches'

const job = (id: number, over: Partial<BulkJobEntry> = {}): BulkJobEntry => ({
  bulk_job_id: id, engine: 'claude', model: null, kind: 'translate', stage: null, pipeline_id: null,
  status: 'submitted', pending: true, cancellable: true, line_count: 10, scheduled_for: null,
  result_summary: null, last_error: null, submitted_at: null, updated_at: null, ...over,
})

describe('bulk batches helpers', () => {
  it('labels statuses, kinds and resume states in plain words', () => {
    expect(statusLabel('submitted')).toBe('Waiting for the provider')
    expect(statusLabel('auth_error')).toMatch(/Settings/)
    expect(statusLabel('weird')).toBe('weird')
    expect([statusTone('applied'), statusTone('auth_error'), statusTone('cancelled')]).toEqual(['ok', 'bad', ''])
    expect(kindLabel({ kind: 'translate', stage: null })).toBe('Translation')
    expect(kindLabel({ kind: 'reflect', stage: 'critique' })).toBe('Reflect (critique)')
    expect(resumeText([])).toBe('No pending batches to check.')
    expect(resumeText([{ bulk_job_id: 3, state: 'needs_key' }, { bulk_job_id: 4, state: 'polling' }])).toBe(
      '#3 needs an API key in Settings · #4 now being checked',
    )
  })

  it('splits pending from finished and replaces one entry after a cancel', () => {
    const jobs = [job(1), job(2, { status: 'applied', pending: false, cancellable: false }), job(3)]
    const { pending, finished } = splitJobs(jobs)
    expect(pending.map((j) => j.bulk_job_id)).toEqual([1, 3])
    expect(finished.map((j) => j.bulk_job_id)).toEqual([2])
    const next = replaceJob(jobs, job(3, { status: 'cancelled', pending: false, cancellable: false }))
    expect(next.map((j) => j.status)).toEqual(['submitted', 'applied', 'cancelled'])
    expect(next[0]).toBe(jobs[0])
  })

  it('summarises non-zero counts only', () => {
    expect(summaryText(null)).toBe('')
    expect(summaryText({ applied: 120, missing: 0, kept_your_edit: 2, note: 'x' })).toBe('applied 120 · kept your edit 2')
  })

  it('keeps safe server text, drops an unsafe trailing detail, else falls back', () => {
    expect(plainServerText(null, 'Cancelled.')).toBe('Cancelled.')
    expect(plainServerText('Cancelled at the provider and here.', 'x')).toBe('Cancelled at the provider and here.')
    expect(plainServerText('Stopped here, but the call failed. (/home/kae/secret.txt)', 'x')).toBe(
      'Stopped here, but the call failed.',
    )
    expect(plainServerText('api_key=abc', 'Failed.')).toBe('Failed.')
  })

  it('shortens ISO timestamps', () => {
    expect(shortTime('2026-09-29T10:15:42.123')).toBe('2026-09-29 10:15')
    expect(shortTime(null)).toBe('')
    expect(shortTime('soon')).toBe('soon')
  })
})
