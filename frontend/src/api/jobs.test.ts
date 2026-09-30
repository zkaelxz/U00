import { describe, expect, it, vi } from 'vitest'

import { ApiError } from './client'
import { getJobStages } from './jobs'

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
