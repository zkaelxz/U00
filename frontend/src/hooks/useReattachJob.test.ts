import { describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api/client'
import type { JobRecord } from '../types/jobs'
import { adoptRun } from './useJob'
import { reattachActiveJob, STALE_JOB_SECONDS } from './useReattachJob'

const NOW_MS = 1_800_000_000_000
const now = () => NOW_MS

const job = (job_id: string, status: string, updated_at = NOW_MS / 1000 - 30): JobRecord => ({
  job_id, status, progress: null, message: '', error: null, description: null,
  gpu_touching: false, started_at: null, finished_at: null, updated_at,
})

const notFound = () => new ApiError(404, { code: 'not_found', message: 'No job.' })

function jobs(map: Record<string, string>) {
  return vi.fn(async (id: string) => {
    if (!(id in map)) throw notFound()
    return job(id, map[id])
  })
}

const flush = () => new Promise((r) => setTimeout(r, 0))

describe('reattachActiveJob', () => {
  it.each(['queued', 'running'])('attaches to a %s job', async (status) => {
    const attach = vi.fn()
    reattachActiveJob(['translate_3'], { fetchJob: jobs({ translate_3: status }), attach, now })
    await flush()
    expect(attach).toHaveBeenCalledWith('translate_3')
  })

  it('skips missing (404) and finished jobs', async () => {
    const attach = vi.fn()
    const fetchJob = jobs({ a: 'done', b: 'error', c: 'cancelled' })
    reattachActiveJob(['a', 'b', 'c', 'd'], { fetchJob, attach, now })
    await flush()
    expect(fetchJob).toHaveBeenCalledTimes(4)
    expect(attach).not.toHaveBeenCalled()
  })

  it('picks the first active id in list order, whatever order the reads settle in', async () => {
    const attach = vi.fn()
    const fetchJob = vi.fn((id: string) =>
      new Promise<JobRecord>((res) => setTimeout(() => res(job(id, id === 'old' ? 'done' : 'running')), id === 'first' ? 5 : 0)))
    reattachActiveJob(['old', 'first', 'second'], { fetchJob, attach, now })
    await new Promise((r) => setTimeout(r, 20))
    expect(attach).toHaveBeenCalledTimes(1)
    expect(attach).toHaveBeenCalledWith('first')
  })

  it('ignores a failed read (network) instead of surfacing it', async () => {
    const attach = vi.fn()
    const fetchJob = vi.fn(async (id: string) => {
      if (id === 'a') throw new Error('offline')
      return job(id, 'running')
    })
    reattachActiveJob(['a', 'b'], { fetchJob, attach, now })
    await flush()
    expect(attach).toHaveBeenCalledWith('b')
  })

  it('skips a running record left behind by a dead process (no heartbeat for 15 minutes)', async () => {
    const attach = vi.fn()
    const fetchJob = vi.fn(async (id: string) =>
      job(id, 'running', id === 'dead' ? NOW_MS / 1000 - STALE_JOB_SECONDS - 1 : NOW_MS / 1000 - 60))
    reattachActiveJob(['dead', 'live'], { fetchJob, attach, now })
    await flush()
    expect(attach).toHaveBeenCalledTimes(1)
    expect(attach).toHaveBeenCalledWith('live')
  })

  it('does not attach after cancel (unmount or drama change)', async () => {
    const attach = vi.fn()
    const cancel = reattachActiveJob(['x'], { fetchJob: jobs({ x: 'running' }), attach, now })
    cancel()
    await flush()
    expect(attach).not.toHaveBeenCalled()
  })
})

describe('adoptRun', () => {
  it('adopts a found job only while no run is tracked', () => {
    expect(adoptRun({ id: null, key: 2 }, 'translate_1')).toEqual({ id: 'translate_1', key: 3 })
    const own = { id: 'bulk_translate_1', key: 3 }
    // A run the user started first always wins, however the lookup and the start interleave.
    expect(adoptRun(own, 'translate_1')).toBe(own)
  })
})
