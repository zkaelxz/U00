import { describe, expect, it } from 'vitest'

import { ApiError, artifactUrl, deleteJson, getJson, postJson, postMultipart } from './client'
import { cancelJob, getJob, listJobs } from './jobs'

interface Call {
  url: string
  init?: RequestInit
}

function fakeFetch(status: number, body: unknown, calls: Call[] = []): typeof fetch {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init })
    return new Response(typeof body === 'string' ? body : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  }) as typeof fetch
}

const fail = (p: Promise<unknown>) =>
  p.then(
    () => {
      throw new Error('expected a failure')
    },
    (e) => e as ApiError,
  )

const errBody = { error: { code: 'conflict', message: 'A job is already running.', details: { job_id: 'j1' } } }

describe('write helpers', () => {
  it('postJson sends a JSON body and parses the reply', async () => {
    const calls: Call[] = []
    await expect(postJson('/api/x', { a: 1 }, fakeFetch(200, { ok: true }, calls))).resolves.toEqual({ ok: true })
    expect(calls[0].init?.method).toBe('POST')
    expect(calls[0].init?.body).toBe('{"a":1}')
    expect((calls[0].init!.headers as Record<string, string>)['Content-Type']).toBe('application/json')
  })

  it('postJson without a body sends none', async () => {
    const calls: Call[] = []
    await postJson('/api/x', undefined, fakeFetch(200, {}, calls))
    expect(calls[0].init?.body).toBeUndefined()
  })

  it('deleteJson uses DELETE', async () => {
    const calls: Call[] = []
    await deleteJson('/api/x/1', fakeFetch(200, { deleted: true }, calls))
    expect(calls[0].init?.method).toBe('DELETE')
  })

  it('postMultipart passes FormData without a Content-Type', async () => {
    const calls: Call[] = []
    const form = new FormData()
    form.append('file', new Blob(['x']), 'a.txt')
    await postMultipart('/api/up', form, fakeFetch(200, {}, calls))
    expect(calls[0].init?.body).toBe(form)
    expect(calls[0].init!.headers as Record<string, string>).not.toHaveProperty('Content-Type')
    // local_only upload routes refuse multipart without this header
    expect((calls[0].init!.headers as Record<string, string>)['X-Baihe-Local']).toBe('1')
  })

  it.each([
    ['GET', (f: typeof fetch) => getJson('/a', f)],
    ['POST', (f: typeof fetch) => postJson('/a', {}, f)],
    ['DELETE', (f: typeof fetch) => deleteJson('/a', f)],
    ['multipart', (f: typeof fetch) => postMultipart('/a', new FormData(), f)],
  ])('%s parses the API error shape', async (_name, call) => {
    const err = await fail(call(fakeFetch(409, errBody)))
    expect(err).toBeInstanceOf(ApiError)
    expect(err.status).toBe(409)
    expect(err.code).toBe('conflict')
    expect(err.details).toEqual({ job_id: 'j1' })
  })

  it('maps a non-JSON failure and a network failure', async () => {
    const e1 = await fail(postJson('/a', {}, fakeFetch(502, '<html>bad gateway</html>')))
    expect(e1.code).toBe('internal_error')
    const boom = (async () => { throw new TypeError('x') }) as unknown as typeof fetch
    const e2 = await fail(deleteJson('/a', boom))
    expect(e2.code).toBe('network_error')
    expect(e2.status).toBe(0)
  })

  it('artifactUrl is a plain download path', () => {
    expect(artifactUrl(3, 'srt')).toBe('/api/artifacts/dramas/3/srt')
  })
})

describe('jobs api', () => {
  it('getJob, listJobs and cancelJob hit the right endpoints', async () => {
    const calls: Call[] = []
    await getJob('a b', fakeFetch(200, {}, calls))
    await listJobs(fakeFetch(200, { items: [], count: 0 }, calls))
    await cancelJob('j1', fakeFetch(200, {}, calls))
    expect(calls.map((c) => [c.init?.method ?? 'GET', c.url])).toEqual([
      ['GET', '/api/jobs/a%20b'],
      ['GET', '/api/jobs'],
      ['POST', '/api/jobs/j1/cancel'],
    ])
  })
})
