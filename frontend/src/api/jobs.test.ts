import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from './client'
import { clearFinishedJobs, deleteJob, getJobStages } from './jobs'
import { getPcMode, resetPcModeForTests } from './pcOnly'

function reply(status: number, body: unknown) {
  const mock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }))
  return { mock, f: mock as unknown as typeof fetch }
}

describe('jobs api: stage timing', () => {
  it('GETs /api/jobs/{id}/stages with the id encoded', async () => {
    const body = { job_id: 'a/b', runs: [] }
    const { mock, f } = reply(200, body)
    const out = await getJobStages('a/b', f)
    expect(mock.mock.calls[0][0]).toBe('/api/jobs/a%2Fb/stages')
    expect((mock.mock.calls[0][1] as RequestInit).method ?? 'GET').toBe('GET')
    expect(out).toEqual(body)
  })

  it('a 404 rejects with an ApiError', async () => {
    const { f } = reply(404, { error: { code: 'not_found', message: "No job with id 'x'." } })
    const err = await getJobStages('x', f).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(404)
  })
})

describe('jobs api: PC-only erasing', () => {
  afterEach(() => resetPcModeForTests())

  it.each([
    ['deleteJob', (f: typeof fetch) => deleteJob('a/b', f), '/api/jobs/a%2Fb/delete'],
    ['clearFinishedJobs', (f: typeof fetch) => clearFinishedJobs(f), '/api/jobs/clear-finished'],
  ])('%s sends the PC header and confirm', async (_n, call, url) => {
    const { mock, f } = reply(200, {})
    await call(f)
    expect(mock.mock.calls[0][0]).toBe(url)
    const init = mock.mock.calls[0][1] as RequestInit
    expect(new Headers(init.headers).get('X-Baihe-Local')).toBe('1')
    expect(JSON.parse(init.body as string)).toEqual({ confirm: true })
  })

  it.each([
    ['deleteJob', (f: typeof fetch) => deleteJob('j1', f)],
    ['clearFinishedJobs', (f: typeof fetch) => clearFinishedJobs(f)],
  ])('a 403 from %s marks the tab remote', async (_n, call) => {
    const { f } = reply(403, { error: { code: 'local_only', message: 'PC only' } })
    await expect(call(f)).rejects.toBeInstanceOf(ApiError)
    expect(getPcMode()).toBe('remote')
  })
})
