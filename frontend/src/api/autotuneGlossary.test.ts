import { describe, expect, it } from 'vitest'

import {
  applyAutotune,
  applyLinesGlossary,
  applyNovelGlossary,
  getAutotune,
  getLinesGlossary,
  getNovelGlossary,
  startAutotune,
  startLinesGlossary,
  startNovelGlossary,
} from './autotuneGlossary'
import { ApiError } from './client'

type Call = { url: string; init?: RequestInit }

function fakeFetch(calls: Call[], status = 200, body: unknown = {}) {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init })
    return new Response(JSON.stringify(body), { status })
  }) as typeof fetch
}

describe('auto-tune + glossary-from-novel api', () => {
  it('uses the batch 2C routes with the schema field names', async () => {
    const calls: Call[] = []
    const f = fakeFetch(calls)
    await getAutotune(4, f)
    await startAutotune(4, { initial_prompt: 'names' }, f)
    await applyAutotune(4, 800, f)
    await getNovelGlossary(4, f)
    await startNovelGlossary(4, f)
    await applyNovelGlossary(4, { terms: ['魏婴'] }, f)
    expect(calls.map((c) => [c.url, c.init?.method ?? 'GET'])).toEqual([
      ['/api/transcribe/dramas/4/autotune', 'GET'],
      ['/api/transcribe/dramas/4/autotune', 'POST'],
      ['/api/transcribe/dramas/4/autotune/apply', 'POST'],
      ['/api/glossary/dramas/4/from-novel', 'GET'],
      ['/api/glossary/dramas/4/from-novel', 'POST'],
      ['/api/glossary/dramas/4/from-novel/apply', 'POST'],
    ])
    expect(JSON.parse(String(calls[1].init?.body))).toEqual({ initial_prompt: 'names' })
    // The apply body is candidate_ms (AutotuneApplyRequest), nothing else.
    expect(JSON.parse(String(calls[2].init?.body))).toEqual({ candidate_ms: 800 })
    expect(calls[4].init?.body).toBeUndefined()
    expect(JSON.parse(String(calls[5].init?.body))).toEqual({ terms: ['魏婴'] })
  })

  it('uses the from-lines routes and sends overrides keyed by term', async () => {
    const calls: Call[] = []
    const f = fakeFetch(calls)
    await getLinesGlossary(4, f)
    await startLinesGlossary(4, f)
    await applyLinesGlossary(4, { terms: ['魏婴'], overrides: { 魏婴: { translation: 'Wei Ying' } } }, f)
    await applyNovelGlossary(4, { terms: ['魏婴'], overrides: { 魏婴: { policy: 'hybrid' } } }, f)
    expect(calls.map((c) => [c.url, c.init?.method ?? 'GET'])).toEqual([
      ['/api/glossary/dramas/4/from-lines', 'GET'],
      ['/api/glossary/dramas/4/from-lines', 'POST'],
      ['/api/glossary/dramas/4/from-lines/apply', 'POST'],
      ['/api/glossary/dramas/4/from-novel/apply', 'POST'],
    ])
    expect(calls[1].init?.body).toBeUndefined()
    expect(JSON.parse(String(calls[2].init?.body))).toEqual({ terms: ['魏婴'], overrides: { 魏婴: { translation: 'Wei Ying' } } })
    expect(JSON.parse(String(calls[3].init?.body))).toEqual({ terms: ['魏婴'], overrides: { 魏婴: { policy: 'hybrid' } } })
  })

  it('surfaces a paid-engine 403 as an ApiError with the status', async () => {
    const f = fakeFetch([], 403, { error: { code: 'forbidden', message: 'Not allowed.' } })
    await expect(startNovelGlossary(1, f)).rejects.toMatchObject({ status: 403, code: 'forbidden' })
    await expect(startNovelGlossary(1, f)).rejects.toBeInstanceOf(ApiError)
  })
})
