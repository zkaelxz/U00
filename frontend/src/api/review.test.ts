import { describe, expect, it } from 'vitest'

import { buildPatch, draftFromLine } from '../pages/workspace/stages/review/reviewDraft'
import { formatTime, pageCount, staleLabels, suggestionIsStale, suggestionPatch } from '../pages/workspace/stages/review/reviewLogic'
import type { ReviewLine } from '../types/review'
import { ApiError } from './client'
import * as review from './review'

const line: ReviewLine = {
  id: 7, idx: 3, start: 1, end: 2.5, zh: '你好', en: 'Hello', speaker: null, speaker_manual: false,
  sfx: false, flag: null, flag_note: null, dub_filename: null, lang: null, reviewed: false, confidence: null, mark: null, unsure_words: null,
}

function fakeFetch(status: number, body: unknown, calls: { url: string; init?: RequestInit }[] = []) {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init })
    return new Response(JSON.stringify(body), { status })
  }) as typeof fetch
}

describe('review logic', () => {
  it('sends nothing when the draft is unchanged', () => {
    expect(buildPatch(line, draftFromLine(line))).toBeNull()
  })
  it('sends changed fields with their old values as expected', () => {
    const patch = buildPatch(line, { ...draftFromLine(line), en: 'Hi', speaker: ' Ann ' })
    expect(patch).toEqual({ en: 'Hi', speaker: 'Ann', expected: { en: 'Hello', speaker: null } })
  })
  it('rejects bad timing', () => {
    expect(buildPatch(line, { ...draftFromLine(line), end: 'x' })).toMatch(/number/)
    expect(buildPatch(line, { ...draftFromLine(line), start: '3' })).toMatch(/after/)
  })
  it('formats times and pages', () => {
    expect(formatTime(65.5)).toBe('1:05.50')
    expect(pageCount(0)).toBe(1)
    expect(pageCount(81)).toBe(3)
  })
  it('labels stale ids by line number', () => {
    const m = { id: 7, idx: 2, old_text: 'a', new_text: 'b' } // idx is 0-based: line #3
    expect(staleLabels([7, 9], [m])).toEqual(['#3', 'line id 9'])
  })
})

describe('review api', () => {
  it('posts set-language by ids or by speaker', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    const ok = { updated: 1, line_ids: [7], skipped_ids: [] }
    await review.setLinesLanguage(1, { lang: 'ko', line_ids: [7] }, fakeFetch(200, ok, calls))
    await review.setLinesLanguage(1, { lang: null, speaker: 'Ann' }, fakeFetch(200, ok, calls))
    expect(calls.map((c) => c.url)).toEqual(['/api/lines/dramas/1/set-language', '/api/lines/dramas/1/set-language'])
    expect(calls.map((c) => JSON.parse(String(c.init?.body)))).toEqual([
      { lang: 'ko', line_ids: [7] },
      { lang: null, speaker: 'Ann' },
    ])
  })
  it('builds list and search urls', async () => {
    const calls: { url: string }[] = []
    await review.listLines(1, 2, 40, 'flagged', fakeFetch(200, {}, calls))
    await review.searchLines(1, 'a b', fakeFetch(200, [], calls))
    await review.searchLines(1, 'a b', fakeFetch(200, [], calls), 200)
    expect(calls.map((c) => c.url)).toEqual([
      '/api/review/dramas/1/lines?page=2&page_size=40&only=flagged',
      '/api/review/dramas/1/search?term=a%20b&limit=200',
      '/api/review/dramas/1/search?term=a%20b&limit=200&offset=200',
    ])
  })
  it('posts a patch and surfaces a 409 as ApiError', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    const f = fakeFetch(409, { error: { code: 'conflict', message: 'changed' } }, calls)
    const err = await review.patchLine(1, 7, { en: 'x', expected: { en: 'y' } }, f).catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err.status).toBe(409)
    expect(calls[0].url).toBe('/api/lines/dramas/1/lines/7')
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ en: 'x', expected: { en: 'y' } })
  })
  it('applies only id/old/new of each match', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    const none = { applied: 0, stale: 0, applied_ids: [], stale_ids: [] }
    await review.applyFindReplace(1, [{ id: 7, idx: 3, old_text: 'a', new_text: 'b' }], fakeFetch(200, none, calls))
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ matches: [{ id: 7, old_text: 'a', new_text: 'b' }] })
  })
  it('applies more matches than one request takes in chunks and adds up the results', async () => {
    const bodies: { id: number }[][] = []
    const f = (async (_input: RequestInfo | URL, init?: RequestInit) => {
      const sent = (JSON.parse(String(init?.body)) as { matches: { id: number }[] }).matches
      bodies.push(sent)
      // The server reports the first line of each request as edited since Preview.
      const [first, ...rest] = sent.map((m) => m.id)
      return new Response(JSON.stringify({ applied: rest.length, stale: 1, applied_ids: rest, stale_ids: [first] }), { status: 200 })
    }) as typeof fetch
    const matches = Array.from({ length: 2500 }, (_, i) => ({ id: i + 1, idx: i, old_text: 'Lin', new_text: 'Lyn' }))
    const r = await review.applyFindReplace(1, matches, f)
    expect(bodies.map((b) => b.length)).toEqual([1000, 1000, 500])
    expect(r.applied).toBe(2497)
    expect(r.stale).toBe(3)
    expect(r.stale_ids).toEqual([1, 1001, 2001])
    expect(r.applied_ids).toHaveLength(2497)
  })
  it('a failed later request reports what was applied and what is left instead of throwing', async () => {
    let n = 0
    const f = (async (_input: RequestInfo | URL, init?: RequestInit) => {
      n += 1
      if (n === 2) return new Response(JSON.stringify({ error: { code: 'internal_error', message: 'boom' } }), { status: 500 })
      const ids = (JSON.parse(String(init?.body)) as { matches: { id: number }[] }).matches.map((m) => m.id)
      return new Response(JSON.stringify({ applied: ids.length, stale: 0, applied_ids: ids, stale_ids: [] }), { status: 200 })
    }) as typeof fetch
    const matches = Array.from({ length: 2500 }, (_, i) => ({ id: i + 1, idx: i, old_text: 'Lin', new_text: 'Lyn' }))
    const r = await review.applyFindReplace(1, matches, f)
    expect(n).toBe(2)
    expect(r.applied).toBe(1000)
    expect(r.unsent).toBe(1500)
    expect(r.error).toBeInstanceOf(ApiError)
    expect(review.unsentNote(r)).toBe(' Stopped partway: 1500 lines were not changed. Preview again and apply to finish them.')
    // Nothing applied yet: the error is the answer.
    n = 1
    await expect(review.applyFindReplace(1, matches, f)).rejects.toBeInstanceOf(ApiError)
  })
  it('starts a job and deletes a note', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    await review.startReviewJob(1, 'fix-flagged', undefined, fakeFetch(200, {}, calls))
    await review.deleteNote(1, 4, fakeFetch(200, {}, calls))
    expect(calls.map((c) => [c.init?.method, c.url])).toEqual([
      ['POST', '/api/review-jobs/dramas/1/fix-flagged'],
      ['DELETE', '/api/lines/dramas/1/notes/4'],
    ])
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({})
  })
  it('sends engine, model and cap for a fix-flagged run', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    await review.startReviewJob(1, 'fix-flagged', { engine: 'openai', model: 'm', job_cost_cap_usd: 0.5 }, fakeFetch(200, {}, calls))
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ engine: 'openai', model: 'm', job_cost_cap_usd: 0.5 })
  })
  it('sends bulk for a review check and reads it back (R49)', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    const started = { job_id: 'bulk_consistency_1', drama_id: 1, kind: 'consistency', engine: 'claude', model: null, line_count: 4, bulk: true }
    const r = await review.startReviewJob(1, 'consistency', { bulk: true }, fakeFetch(200, started, calls))
    expect(calls[0].url).toBe('/api/review-jobs/dramas/1/consistency')
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ bulk: true })
    expect(r.bulk).toBe(true)
  })
  it('reads stored results and checks from the review routes', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    const f = fakeFetch(200, {}, calls)
    await review.getConsistency(2, f)
    await review.getEmotions(2, f)
    await review.getTendencies(2, f)
    await review.compareVersions(2, 5, 6, f)
    await review.getCoverage(2, f)
    await review.getPacingFlags(2, f)
    await review.getLineProvenance(2, 9, f)
    await review.getLineOriginalText(2, 9, f)
    expect(calls.map((c) => c.url)).toEqual([
      '/api/review/dramas/2/consistency',
      '/api/review/dramas/2/emotions',
      '/api/review/dramas/2/tendencies',
      '/api/review/dramas/2/versions/compare?left_id=5&right_id=6',
      '/api/review/dramas/2/coverage',
      '/api/review/dramas/2/pacing-flags',
      '/api/review/dramas/2/lines/9/provenance',
      '/api/review/dramas/2/lines/9/original-text',
    ])
    expect(calls.every((c) => (c.init?.method ?? 'GET') === 'GET')).toBe(true)
    expect(review.notesMarkdownUrl(2)).toMatch(/\/api\/review\/dramas\/2\/notes\/markdown$/)
  })
  it('surfaces a 404 from a result route as ApiError', async () => {
    const err = await review.getConsistency(99, fakeFetch(404, { error: { code: 'not_found', message: 'x' } })).catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err.status).toBe(404)
  })
})

describe('line AI', () => {
  it('builds a compare-and-set patch for a suggestion', () => {
    expect(suggestionPatch(line, 'Hi')).toEqual({ en: 'Hi', expected: { en: 'Hello' } })
    expect(suggestionPatch(line, 'Hello')).toBeNull()
  })
  it('flags a suggestion made for an older translation as stale', () => {
    expect(suggestionIsStale(line, 'Hello')).toBe(false)
    expect(suggestionIsStale(line, 'Old')).toBe(true)
  })
  it('posts to the improve and explain endpoints without engine or keys', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    const out = { line_id: 7, current_en: 'Hello', suggestion: 'Hi', changed: true, engine: 'x', model: null }
    await review.improveLine(1, 7, ' softer ', undefined, fakeFetch(200, out, calls))
    expect(calls[0].url).toBe('/api/line-ai/dramas/1/lines/7/improve')
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ issue: 'softer' })
    await review.explainLine(1, 7, undefined, fakeFetch(200, { line_id: 7, explanation: 'e', engine: 'x', model: null }, calls))
    expect(calls[1].url).toBe('/api/line-ai/dramas/1/lines/7/explain')
    expect(JSON.parse(String(calls[1].init?.body))).toEqual({})
  })
  it('sends the picked engine and model, and never a model without an engine', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    await review.improveLine(1, 7, '', { engine: 'deepseek', model: 'deepseek-chat' }, fakeFetch(200, {}, calls))
    await review.explainLine(1, 7, { engine: 'ollama' }, fakeFetch(200, {}, calls))
    await review.lineGrammar(1, 7, { engine: '', model: 'stray' }, fakeFetch(200, {}, calls))
    expect(calls.map((c) => JSON.parse(String(c.init?.body)))).toEqual([
      { issue: '', engine: 'deepseek', model: 'deepseek-chat' },
      { engine: 'ollama' },
      {},
    ])
  })
})
