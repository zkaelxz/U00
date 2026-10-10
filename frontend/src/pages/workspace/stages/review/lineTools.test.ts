import { describe, expect, it } from 'vitest'

import * as review from '../../../../api/review'
import type { TmSuggestion } from '../../../../types/review'
import { isToolMode } from './reviewLogic'
import { dismissTm, readDismissed, tmDismissKey, visibleTm } from './tmDismiss'

function fakeFetch(status: number, body: unknown, calls: { url: string; init?: RequestInit }[] = [], raw = false) {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init })
    return new Response(raw ? (body as BodyInit) : JSON.stringify(body), { status })
  }) as typeof fetch
}

function memoryStore() {
  const m = new Map<string, string>()
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v) }
}

const tm = (over: Partial<TmSuggestion> = {}): TmSuggestion => ({
  line_id: 5, line_idx: 4, zh: ' 你好 ', en: 'hello', suggestion: 'hi there', similarity: 0.9, exact: false, entry_id: 3,
  ...over,
})

describe('per-line tool api', () => {
  it('posts alternatives and grammar with an empty body (engine chosen on the PC)', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    await review.lineAlternatives(2, 9, fakeFetch(200, {}, calls))
    await review.lineGrammar(2, 9, fakeFetch(200, {}, calls))
    expect(calls.map((c) => c.url)).toEqual([
      '/api/line-ai/dramas/2/lines/9/alternatives',
      '/api/line-ai/dramas/2/lines/9/grammar',
    ])
    expect(calls.every((c) => c.init?.method === 'POST' && c.init?.body === '{}')).toBe(true)
  })

  it('shortens every overlong line, or only the given ids', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    await review.shortenOverlong(2, undefined, fakeFetch(200, {}, calls))
    await review.shortenOverlong(2, [4, 5], fakeFetch(200, {}, calls))
    expect(calls[0].url).toBe('/api/lines/dramas/2/shorten-overlong')
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ confirm: true })
    expect(JSON.parse(String(calls[1].init?.body))).toEqual({ line_ids: [4, 5], confirm: true })
  })

  it('accepts a translation-memory entry against the English it saw', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    await review.acceptTm(2, 9, 11, 'Old text', fakeFetch(200, {}, calls))
    expect(calls[0].url).toBe('/api/lines/dramas/2/lines/9/accept-tm')
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ entry_id: 11, expected_en: 'Old text' })
  })

  it('asks for the nearest flagged line with the view it is in', async () => {
    const calls: { url: string }[] = []
    await review.flaggedAdjacent(2, 'next', 7, 40, 'untranslated', fakeFetch(200, {}, calls))
    await review.flaggedAdjacent(2, 'prev', null, 40, 'all', fakeFetch(200, {}, calls))
    expect(calls.map((c) => c.url)).toEqual([
      '/api/review/dramas/2/flagged-adjacent?direction=next&page_size=40&only=untranslated&from_line_id=7',
      '/api/review/dramas/2/flagged-adjacent?direction=prev&page_size=40&only=all',
    ])
  })

  it('limits translation-memory suggestions to the given lines', async () => {
    const calls: { url: string }[] = []
    await review.listTmSuggestions(2, fakeFetch(200, [], calls))
    await review.listTmSuggestions(2, fakeFetch(200, [], calls), [4, 5])
    expect(calls.map((c) => c.url)).toEqual([
      '/api/review/dramas/2/tm-suggestions',
      '/api/review/dramas/2/tm-suggestions?line_id=4&line_id=5',
    ])
  })
})

describe('translation-memory dismiss', () => {
  it('hides the dismissed source/suggestion pair on every line, per title', () => {
    const store = memoryStore()
    dismissTm(1, tm(), store)
    const dismissed = readDismissed(1, store)
    expect(dismissed.has(tmDismissKey(tm({ zh: '你好' })))).toBe(true)
    const list = [tm(), tm({ line_id: 6, zh: '你好' }), tm({ suggestion: 'hello!' })]
    expect(visibleTm(list, dismissed).map((s) => s.suggestion)).toEqual(['hello!'])
    expect(readDismissed(2, store).size).toBe(0)
  })

  it('treats unreadable storage as nothing dismissed', () => {
    const bad = { getItem: () => '{not json', setItem: () => undefined }
    expect(readDismissed(1, bad).size).toBe(0)
  })

  it('keeps dismissals in memory when storage is unavailable', () => {
    dismissTm(42, tm(), null)
    expect(readDismissed(42, null).has(tmDismissKey(tm()))).toBe(true)
  })
})

describe('panel modes', () => {
  it('tells the study tools from the AI panel', () => {
    expect(['alternatives', 'grammar'].every((m) => isToolMode(m as never))).toBe(true)
    expect(isToolMode('improve')).toBe(false)
    expect(isToolMode('explain')).toBe(false)
  })
})
