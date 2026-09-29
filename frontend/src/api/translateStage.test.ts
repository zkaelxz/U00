import { describe, expect, it } from 'vitest'

import { ApiError } from './client'
import {
  applyVoiceBankEntry,
  applyWorkflowTier,
  buildEstimateQuery,
  cancelBulkTranslation,
  deleteGlossaryTerms,
  getTranslateEstimate,
  listBulkTranslations,
  saveCharacter,
  saveGlossaryTerm,
  saveInstructions,
  saveTranslatePreset,
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

  it('bulk-deletes terms one by one with confirm=true and reports failures', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    const f = (async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), init })
      const ok = !String(input).includes('/terms/2?')
      return new Response(JSON.stringify(ok ? { deleted: true } : { detail: 'gone' }), { status: ok ? 200 : 404 })
    }) as typeof fetch
    const r = await deleteGlossaryTerms(7, [1, 2, 3], f)
    expect(calls.map((c) => [c.init?.method, c.url])).toEqual([
      ['DELETE', '/api/glossary/dramas/7/terms/1?confirm=true'],
      ['DELETE', '/api/glossary/dramas/7/terms/2?confirm=true'],
      ['DELETE', '/api/glossary/dramas/7/terms/3?confirm=true'],
    ])
    expect(r.deleted).toEqual([1, 3])
    expect(r.failed.map((x) => x.id)).toEqual([2])
    expect(r.failed[0].error).toBeInstanceOf(ApiError)
  })

  it('posts a voice bank apply body', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    await applyVoiceBankEntry(7, 'SPEAKER 1', 5, fakeFetch(200, {}, calls))
    expect(calls[0].url).toBe('/api/characters/dramas/7/voice-bank/apply')
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ speaker_label: 'SPEAKER 1', voice_bank_id: 5 })
  })

  it('lists bulk batches with GET and cancels one with POST', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    const f = fakeFetch(200, { drama_id: 3, jobs: [] }, calls)
    const list = await listBulkTranslations(3, f)
    await cancelBulkTranslation(3, 12, f)
    expect(list.jobs).toEqual([])
    expect(calls[0].url).toBe('/api/translate-run/dramas/3/bulk')
    expect(calls[0].init?.method ?? 'GET').toBe('GET')
    expect(calls[1].url).toBe('/api/translate-run/dramas/3/bulk/12/cancel')
    expect(calls[1].init?.method).toBe('POST')
  })

  it('surfaces a 409 on cancel (batch already finished) as an ApiError', async () => {
    const f = fakeFetch(409, { error: { code: 'conflict', message: 'Bulk job 4 is applied and cannot be cancelled.' } }, [])
    const err = await cancelBulkTranslation(1, 4, f).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(409)
  })
})

describe('workflow tier and preset writes', () => {
  it('posts the tier and the preset body to their routes', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    const f = fakeFetch(200, {}, calls)
    await applyWorkflowTier(6, 'release', f)
    await saveTranslatePreset({
      name: 'Mine', translation_engine: 'claude', engine_model: null, style_preset: null,
      locale: 'en-US', default_female_pronouns: false, include_genre_notes: true,
    }, f)
    expect(calls[0].url).toBe('/api/translate-run/dramas/6/workflow-tier')
    expect(calls[0].init?.method).toBe('POST')
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ tier: 'release' })
    expect(calls[1].url).toBe('/api/translate-run/presets')
    expect(JSON.parse(String(calls[1].init?.body)).name).toBe('Mine')
  })

  it('surfaces a 409 for a taken preset name', async () => {
    const f = fakeFetch(409, { error: { code: 'conflict', message: 'taken' } }, [])
    const err = await saveTranslatePreset({
      name: 'Mine', translation_engine: 'claude', engine_model: null, style_preset: null,
      locale: null, default_female_pronouns: false, include_genre_notes: true,
    }, f).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(409)
  })
})
