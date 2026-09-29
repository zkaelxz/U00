import { describe, expect, it } from 'vitest'

import { ApiError } from './client'
import * as review from './review'

function fakeFetch(status: number, body: unknown, calls: { url: string; init?: RequestInit }[] = []) {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init })
    return new Response(JSON.stringify(body), { status })
  }) as typeof fetch
}

describe('activate a translation version (R39)', () => {
  it('posts confirm=true to the activate route', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    const out = { drama_id: 2, version_id: 5, label: 'v', activated: true, lines_changed: 3, conflicts: [] }
    const r = await review.activateVersion(2, 5, fakeFetch(200, out, calls))
    expect(r.lines_changed).toBe(3)
    expect(calls[0].url).toBe('/api/review/dramas/2/versions/5/activate')
    expect(calls[0].init?.method).toBe('POST')
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ confirm: true })
  })
  it('surfaces a 409 (job running or lines restructured) as ApiError', async () => {
    const err = await review
      .activateVersion(2, 5, fakeFetch(409, { error: { code: 'conflict', message: 'A background job is still running' } }))
      .catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err.status).toBe(409)
  })
})

describe('retry a content-blocked line (R10)', () => {
  it('sends only the engine name, never a key', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    const line = {
      id: 7, idx: 1, start: 0, end: 1, zh: '再见', en: 'Bye', speaker: null, speaker_manual: false,
      sfx: false, flag: null, flag_note: '', dub_filename: null,
    }
    const out = { drama_id: 1, line_id: 7, engine: 'ollama', model: null, retried: true, blocked: false, reason: null, line }
    const r = await review.retryBlockedLine(1, 7, 'ollama', fakeFetch(200, out, calls))
    expect(r.retried).toBe(true)
    expect(calls[0].url).toBe('/api/lines/dramas/1/lines/7/retry-blocked')
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ engine: 'ollama' })
  })
  it('passes a blocked-again result through', async () => {
    const out = { drama_id: 1, line_id: 7, engine: 'gemini', model: null, retried: false, blocked: true, reason: 'SAFETY', line: {} }
    const r = await review.retryBlockedLine(1, 7, 'gemini', fakeFetch(200, out))
    expect(r.blocked).toBe(true)
    expect(r.reason).toBe('SAFETY')
  })
  it('surfaces a 403 (paid engine not allowed) as ApiError', async () => {
    const err = await review
      .retryBlockedLine(1, 7, 'claude', fakeFetch(403, { error: { code: 'forbidden', message: 'Not allowed.' } }))
      .catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err.status).toBe(403)
  })
})
