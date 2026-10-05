import { describe, expect, it } from 'vitest'

import type { Route } from '../../router'
import type { JobPage, JobRecord } from '../../types/jobs'
import {
  NO_FILTERS, effectiveStatus, filterJobs, jobLinks, kindGroup, normalizeFilters, progressPercent, relativeTime, sortJobs, statusCounts,
  type JobFilters,
} from './jobsFilter'

const NOW = 10_000_000
const job = (o: Partial<JobRecord>): JobRecord => ({
  job_id: 'j', status: 'done', progress: null, message: '', error: null, description: null,
  gpu_touching: false, started_at: NOW - 100, finished_at: NOW - 50, updated_at: NOW - 50, owned_by_me: true, ...o,
})
const titles = new Map([[3, 'Signal'], [4, 'Kae']])
const ids = (jobs: JobRecord[]) => jobs.map((j) => j.job_id)
const f = (o: Partial<JobFilters>): JobFilters => ({ ...NO_FILTERS, ...o })

const sample = [
  job({ job_id: 'done1', description: 'Translate Signal', kind: 'translate', drama_id: 3 }),
  job({ job_id: 'run1', status: 'running', progress: 0.4, finished_at: null, kind: 'transcribe', drama_id: 3 }),
  job({ job_id: 'err1', status: 'error', kind: 'export', drama_id: 4, owned_by_me: false }),
  job({ job_id: 'q1', status: 'queued', started_at: null, finished_at: null, description: 'Dub Kae', kind: 'dub', drama_id: 4 }),
  job({ job_id: 'old1', started_at: NOW - 30 * 86_400, finished_at: NOW - 30 * 86_400 + 5, kind: 'import' }),
  job({ job_id: 'soft', status: 'done', outcome: 'failed', kind: 'other' }),
]

describe('status filter', () => {
  it('counts each chip, keeping Failed and Finished apart', () => {
    expect(statusCounts(sample)).toEqual({ active: 2, failed: 2, finished: 2, all: 6 })
  })
  it('defaults to Active while anything is active, else All', () => {
    expect(effectiveStatus(NO_FILTERS, sample)).toBe('active')
    expect(effectiveStatus(NO_FILTERS, [job({})])).toBe('all')
    expect(effectiveStatus(f({ status: 'failed' }), sample)).toBe('failed')
  })
  it('a done job whose outcome failed counts as failed', () => {
    expect(ids(filterJobs(sample, f({ status: 'failed' }), titles, NOW))).toEqual(['err1', 'soft'])
    expect(ids(filterJobs(sample, f({ status: 'finished' }), titles, NOW))).toEqual(['done1', 'old1'])
  })
})

describe('other filters', () => {
  it('searches the description, the title and the id', () => {
    expect(ids(filterJobs(sample, f({ status: 'all', search: 'kae' }), titles, NOW))).toEqual(['err1', 'q1'])
    expect(ids(filterJobs(sample, f({ status: 'all', search: ' RUN1 ' }), titles, NOW))).toEqual(['run1'])
  })
  it('filters by kind group', () => {
    expect(ids(filterJobs(sample, f({ status: 'all', kind: 'sources' }), titles, NOW))).toEqual(['old1'])
    expect(kindGroup('align')).toBe('other')
    expect(kindGroup(undefined)).toBe('other')
  })
  it('Mine keeps owned jobs only; a missing flag counts as not mine', () => {
    expect(ids(filterJobs([...sample, job({ job_id: 'x', owned_by_me: undefined })], f({ status: 'all', mine: true }), titles, NOW))).not.toContain('err1')
    expect(ids(filterJobs([...sample, job({ job_id: 'x', owned_by_me: undefined })], f({ status: 'all', mine: true }), titles, NOW))).not.toContain('x')
  })
  it('the time range drops old finished jobs but never an active one', () => {
    const stale = job({ job_id: 'oldrun', status: 'running', started_at: NOW - 30 * 86_400, finished_at: null })
    const out = filterJobs([...sample, stale], f({ status: 'all', range: 'week' }), titles, NOW)
    expect(ids(out)).not.toContain('old1')
    expect(ids(out)).toContain('oldrun')
    expect(ids(filterJobs(sample, f({ status: 'all', range: 'today' }), titles, NOW))).toContain('done1')
  })
  it('a queued job is dated by its last update', () => {
    const q = job({ job_id: 'q', status: 'queued', started_at: null, finished_at: null, updated_at: NOW - 10 * 86_400 })
    expect(filterJobs([q], f({ status: 'all', range: 'week' }), titles, NOW)).toHaveLength(1)
  })
})

describe('sorting', () => {
  it('puts active jobs first, then newest started', () => {
    const jobs = [
      job({ job_id: 'a', started_at: 100 }),
      job({ job_id: 'b', started_at: 300 }),
      job({ job_id: 'r', status: 'running', started_at: 50, finished_at: null }),
      job({ job_id: 'c', started_at: 200 }),
    ]
    expect(ids(sortJobs(jobs, 'default', 'desc', NOW))).toEqual(['r', 'b', 'c', 'a'])
  })
  it('puts running jobs before queued ones even when the queued one was updated later', () => {
    const jobs = [
      job({ job_id: 'q', status: 'queued', started_at: null, finished_at: null, updated_at: NOW }),
      job({ job_id: 'r', status: 'running', started_at: NOW - 500, finished_at: null }),
    ]
    expect(ids(sortJobs(jobs, 'default', 'desc', NOW))).toEqual(['r', 'q'])
  })
  it('sorts by started either way, still active first, unstarted last', () => {
    const jobs = [
      job({ job_id: 'a', started_at: 100 }),
      job({ job_id: 'n', status: 'queued', started_at: null, finished_at: null }),
      job({ job_id: 'b', started_at: 300 }),
    ]
    expect(ids(sortJobs(jobs, 'started', 'asc', NOW))).toEqual(['n', 'a', 'b'])
    expect(ids(sortJobs([jobs[0], jobs[2], job({ job_id: 'z', started_at: null, finished_at: null })], 'started', 'desc', NOW))).toEqual(['b', 'a', 'z'])
  })
  it('sorts by duration, counting a running job up to now', () => {
    const jobs = [
      job({ job_id: 'short', started_at: 0, finished_at: 10 }),
      job({ job_id: 'long', started_at: 0, finished_at: 500 }),
      job({ job_id: 'mid', started_at: 0, finished_at: 100 }),
    ]
    expect(ids(sortJobs(jobs, 'duration', 'desc', NOW))).toEqual(['long', 'mid', 'short'])
    expect(ids(sortJobs(jobs, 'duration', 'asc', NOW))).toEqual(['short', 'mid', 'long'])
  })
  it('does not change its input', () => {
    const jobs = [job({ job_id: 'a' }), job({ job_id: 'r', status: 'running' })]
    sortJobs(jobs, 'default', 'desc', NOW)
    expect(ids(jobs)).toEqual(['a', 'r'])
  })
})

describe('links and display helpers', () => {
  it('links a title job to its workspace and the stage its kind belongs to', () => {
    expect(jobLinks({ drama_id: 7, kind: 'translate' })).toEqual({
      title: { name: 'drama', id: 7, stage: null },
      stage: { label: 'Translate', route: { name: 'drama', id: 7, stage: 'translate' } },
    })
    expect(jobLinks({ drama_id: 7, kind: 'transcribe' }).stage?.route).toEqual({ name: 'drama', id: 7, stage: 'source' })
    expect(jobLinks({ drama_id: 7, kind: 'align' }).stage?.label).toBe('Review')
    expect(jobLinks({ drama_id: 7, kind: 'import' }).stage?.label).toBe('Source')
  })
  it('has no stage for a kind without one, and no links without a title', () => {
    expect(jobLinks({ drama_id: 7, kind: 'other' }).stage).toBeNull()
    expect(jobLinks({ drama_id: 7, kind: 'other' }).title).not.toBeNull()
    expect(jobLinks({ drama_id: null, kind: 'translate' })).toEqual({ title: null, stage: null })
    expect(jobLinks({})).toEqual({ title: null, stage: null })
  })
  it('links a title-less job to the page the server names, and nowhere without one', () => {
    const cases: Array<[JobPage, string, Route]> = [
      ['sources', 'Sources', { name: 'sources' }], ['discover', 'Discover', { name: 'discover' }],
      ['live', 'Live', { name: 'live' }], ['settings', 'Settings', { name: 'settings' }],
      ['diagnostics', 'Diagnostics', { name: 'diagnostics' }],
    ]
    for (const [page, label, route] of cases) {
      expect(jobLinks({ drama_id: null, kind: 'other', page })).toEqual({ title: null, stage: { label, route } })
    }
    expect(jobLinks({ drama_id: null, kind: 'other', page: null })).toEqual({ title: null, stage: null })
    expect(jobLinks({ drama_id: null, kind: 'other', page: 'title' })).toEqual({ title: null, stage: null })
    // A titled job keeps its stage link whatever the page says.
    expect(jobLinks({ drama_id: 7, kind: 'translate', page: 'title' }).stage?.route).toEqual({ name: 'drama', id: 7, stage: 'translate' })
  })
  it('shows progress only while a job is active, clamped', () => {
    expect(progressPercent({ status: 'running', progress: 0.404 })).toBe(40)
    expect(progressPercent({ status: 'running', progress: 1.4 })).toBe(100)
    expect(progressPercent({ status: 'running', progress: null })).toBeNull()
    expect(progressPercent({ status: 'done', progress: 1 })).toBeNull()
  })
  it('says how long ago', () => {
    expect(relativeTime(NOW - 5, NOW)).toBe('just now')
    expect(relativeTime(NOW - 300, NOW)).toBe('5 min ago')
    expect(relativeTime(NOW - 7200, NOW)).toBe('2 h ago')
    expect(relativeTime(NOW - 3 * 86_400, NOW)).toBe('3 d ago')
  })
  it('reads stored filters defensively', () => {
    expect(normalizeFilters(null)).toEqual(NO_FILTERS)
    expect(normalizeFilters({ status: 'nope', kind: 'dub', range: 5, mine: 'yes', search: 'x' })).toEqual({ ...NO_FILTERS, kind: 'dub', search: 'x' })
    expect(normalizeFilters({ status: 'failed', mine: true }).status).toBe('failed')
  })
})
