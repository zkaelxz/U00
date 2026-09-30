import { describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api/client'
import type { JobRecord } from '../types/jobs'
import { reattachActiveJob } from './useReattachJob'

const job = (job_id: string, status: string): JobRecord => ({
  job_id, status, progress: null, message: '', error: null, description: null,
  gpu_touching: false, started_at: null, finished_at: null, updated_at: 0,
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
    reattachActiveJob(['translate_3'], { fetchJob: jobs({ translate_3: status }), attach, started: () => false })
    await flush()
    expect(attach).toHaveBeenCalledWith('translate_3')
  })

  it('skips missing (404) and finished jobs', async () => {
    const attach = vi.fn()
    const fetchJob = jobs({ a: 'done', b: 'error', c: 'cancelled' })
    reattachActiveJob(['a', 'b', 'c', 'd'], { fetchJob, attach, started: () => false })
    await flush()
    expect(fetchJob).toHaveBeenCalledTimes(4)
    expect(attach).not.toHaveBeenCalled()
  })

  it('picks the first active id in list order, whatever order the reads settle in', async () => {
    const attach = vi.fn()
    const fetchJob = vi.fn((id: string) =>
      new Promise<JobRecord>((res) => setTimeout(() => res(job(id, id === 'old' ? 'done' : 'running')), id === 'first' ? 5 : 0)))
    reattachActiveJob(['old', 'first', 'second'], { fetchJob, attach, started: () => false })
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
    reattachActiveJob(['a', 'b'], { fetchJob, attach, started: () => false })
    await flush()
    expect(attach).toHaveBeenCalledWith('b')
  })

  it('does not attach once the stage started its own run', async () => {
    const attach = vi.fn()
    reattachActiveJob(['x'], { fetchJob: jobs({ x: 'running' }), attach, started: () => true })
    await flush()
    expect(attach).not.toHaveBeenCalled()
  })

  it('does not attach after cancel (unmount or drama change)', async () => {
    const attach = vi.fn()
    const cancel = reattachActiveJob(['x'], { fetchJob: jobs({ x: 'running' }), attach, started: () => false })
    cancel()
    await flush()
    expect(attach).not.toHaveBeenCalled()
  })
})
