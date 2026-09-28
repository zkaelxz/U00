import { describe, expect, it } from 'vitest'

import { ApiError } from './client'
import {
  buildEstimateQuery,
  getTranslateEstimate,
  saveCharacter,
  saveGlossaryTerm,
  saveInstructions,
  startTranslateRun,
} from './translateStage'
import type { TranslateRunStartBody } from '../types/translateStage'

function fakeFetch(status: number, body: unknown, calls: { url: string; init?: RequestInit }[]) {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init })
    return new Response(JSON.stringify(body), { status })
  }) as typeof fetch
}

const runBody: TranslateRunStartBody = {
  style_note: '',
  locale: 'en-US',
  force_retranslate: false,
  context_window: 5,
  context_window_ahead: 2,
  batch_size: 20,
}

describe('translate stage api', () => {
  it('builds an estimate query from set params only', () => {
    expect(buildEstimateQuery({})).toBe('')
    expect(buildEstimateQuery({ engine: 'openai', force_retranslate: true, job_cost_cap_usd: 0 })).toBe(
      '?engine=openai&force_retranslate=true&job_cost_cap_usd=0',
    )
  })

  it('requests the estimate and posts the run body as JSON', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    const f = fakeFetch(200, { job_id: 'j' }, calls)
    await getTranslateEstimate(4, { engine: 'ollama' }, f)
    await startTranslateRun(4, runBody, f)
    expect(calls[0].url).toBe('/api/translate-run/dramas/4/estimate?engine=ollama')
    expect(calls[1].url).toBe('/api/translate-run/dramas/4/run')
    expect(calls[1].init?.method).toBe('POST')
    expect(JSON.parse(String(calls[1].init?.body)).batch_size).toBe(20)
  })

  it('posts glossary, instruction and character writes to their routes', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    const f = fakeFetch(200, {}, calls)
    await saveGlossaryTerm(2, { term_original: 'a', term_translation: 'b', aliases: [] }, f)
    await saveInstructions(2, 'series', 'be formal', f)
    await saveCharacter(2, { speaker_label: 'A/B c', pronouns: '' }, f)
    expect(calls.map((c) => c.url)).toEqual([
      '/api/glossary/dramas/2/terms',
      '/api/glossary/dramas/2/instructions/series',
      '/api/characters/dramas/2/character',
    ])
    expect(JSON.parse(String(calls[1].init?.body))).toEqual({ text: 'be formal' })
    expect(JSON.parse(String(calls[2].init?.body))).toEqual({ speaker_label: 'A/B c', pronouns: '' })
  })

  it('surfaces a 409 as an ApiError with its status', async () => {
    const f = fakeFetch(409, { error: { code: 'conflict', message: 'running' } }, [])
    const err = await startTranslateRun(1, runBody, f).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(409)
  })
})
