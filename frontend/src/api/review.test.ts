import { describe, expect, it } from 'vitest'

import { buildPatch, draftFromLine, formatTime, pageCount, staleLabels, suggestionIsStale, suggestionPatch } from '../pages/workspace/stages/review/reviewLogic'
import type { ReviewLine } from '../types/review'
import { ApiError } from './client'
import * as review from './review'

const line: ReviewLine = {
  id: 7, idx: 3, start: 1, end: 2.5, zh: '你好', en: 'Hello', speaker: null, speaker_manual: false,
  sfx: false, flag: null, flag_note: null, dub_filename: null,
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
    const m = { id: 7, idx: 3, old_text: 'a', new_text: 'b' }
    expect(staleLabels([7, 9], [m])).toEqual(['#3', 'line id 9'])
  })
})

describe('review api', () => {
  it('builds list and search urls', async () => {
    const calls: { url: string }[] = []
    await review.listLines(1, 2, 40, 'flagged', fakeFetch(200, {}, calls))
    await review.searchLines(1, 'a b', fakeFetch(200, [], calls))
    expect(calls.map((c) => c.url)).toEqual([
      '/api/review/dramas/1/lines?page=2&page_size=40&only=flagged',
      '/api/review/dramas/1/search?term=a%20b&limit=200',
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
    await review.applyFindReplace(1, [{ id: 7, idx: 3, old_text: 'a', new_text: 'b' }], fakeFetch(200, {}, calls))
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ matches: [{ id: 7, old_text: 'a', new_text: 'b' }] })
  })
  it('starts a job and deletes a note', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    await review.startReviewJob(1, 'fix-flagged', fakeFetch(200, {}, calls))
    await review.deleteNote(1, 4, fakeFetch(200, {}, calls))
    expect(calls.map((c) => [c.init?.method, c.url])).toEqual([
      ['POST', '/api/review-jobs/dramas/1/fix-flagged'],
      ['DELETE', '/api/lines/dramas/1/notes/4'],
    ])
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
    await review.improveLine(1, 7, ' softer ', fakeFetch(200, out, calls))
    expect(calls[0].url).toBe('/api/line-ai/dramas/1/lines/7/improve')
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ gemini_free_tier: false, issue: 'softer' })
    await review.explainLine(1, 7, fakeFetch(200, { line_id: 7, explanation: 'e', engine: 'x', model: null }, calls))
    expect(calls[1].url).toBe('/api/line-ai/dramas/1/lines/7/explain')
    expect(JSON.parse(String(calls[1].init?.body))).toEqual({ gemini_free_tier: false })
  })
})
