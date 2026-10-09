import { describe, expect, it } from 'vitest'

import type { DramaSummary } from '../../api/types'
import type { SourceSummary } from '../../types/sources'
import { TIER_TESTS, tierLabel, tierTestLine } from './sourcesFormat'
import { checkSummary, pageUrlProblem, proxyProblem, trackedDramaChoices } from './sourcesSettings'

const drama = (id: number, media_type: string | null) => ({ id, title_en: `D${id}`, media_type }) as unknown as DramaSummary
const source = (comic: boolean) =>
  ({ name: 'x', supports: { get_pages: comic, get_chapter_text: !comic } }) as unknown as SourceSummary

describe('checkSummary', () => {
  it('reads a finished check', () => {
    expect(checkSummary({ checked: 3, new: 0, errors: {}, queued: [] })).toBe('Checked 3 series · no new chapters.')
    expect(checkSummary({ checked: 2, new: 1, errors: { A: 'x' }, queued: ['B'] })).toBe(
      'Checked 2 series · 1 new chapter · importing into 1 drama · 1 failed.',
    )
    expect(checkSummary({ checked: 1, new: 4, errors: {}, queued: ['A', 'B'] })).toBe(
      'Checked 1 series · 4 new chapters · importing into 2 dramas.',
    )
    expect(checkSummary({ checked: 2, new: 3, errors: {}, queued: [], saved: ['A'] })).toBe(
      'Checked 2 series · 3 new chapters · saved 1 series as CBZ.',
    )
  })
  it('says when another check was already running', () => {
    expect(checkSummary({ checked: 0, new: 0, errors: {}, queued: [], skipped: true })).toMatch(/already running/)
  })
})

describe('trackedDramaChoices', () => {
  const dramas = [drama(1, 'novel'), drama(2, 'manhua'), drama(3, 'Manga'), drama(4, null), drama(5, 'drama')]
  it('comic sources feed comic dramas, text sources novels', () => {
    expect(trackedDramaChoices(dramas, source(true)).map((d) => d.id)).toEqual([2, 3])
    expect(trackedDramaChoices(dramas, source(false)).map((d) => d.id)).toEqual([1])
  })
  it('an unknown source gets none', () => {
    expect(trackedDramaChoices(dramas, undefined)).toEqual([])
  })
})

describe('tier tests', () => {
  it('labels follow the details list', () => {
    expect(TIER_TESTS.map((t) => tierLabel(t.tier))).toEqual(['Static', 'Browser', 'Signed-in'])
  })
  it('reads one result', () => {
    const base = { kind: 'tier_test' as const, source: 'x', detail: null, reason: null }
    expect(tierTestLine({ ...base, tier: 'static', ok: true })).toBe('Static: works.')
    expect(tierTestLine({ ...base, tier: 'browser', ok: false, reason: 'NOT_INSTALLED', detail: 'The Playwright package is missing.' })).toBe(
      'Browser: The Playwright package is missing.',
    )
    expect(tierTestLine({ ...base, tier: 'signed_in', ok: false })).toBe('Signed-in: failed.')
  })
})

describe('pageUrlProblem', () => {
  it('accepts http(s) and empty', () => {
    expect(pageUrlProblem('')).toBeNull()
    expect(pageUrlProblem(' https://site.example/book/1 ')).toBeNull()
  })
  it('refuses other text', () => {
    expect(pageUrlProblem('site.example')).toMatch(/full address/)
    expect(pageUrlProblem('ftp://site.example')).toMatch(/http/)
    expect(pageUrlProblem('javascript:alert(1)')).toMatch(/http/)
  })
})

describe('proxyProblem', () => {
  it('accepts host and port only, or empty', () => {
    expect(proxyProblem('')).toBeNull()
    expect(proxyProblem('http://127.0.0.1:8080')).toBeNull()
    expect(proxyProblem('https://user:pw@proxy.example:3128/')).toBeNull()
  })
  it('refuses other schemes and extras', () => {
    expect(proxyProblem('socks5://127.0.0.1:1080')).toMatch(/HTTP/)
    expect(proxyProblem('http://h/path')).toMatch(/address and port/)
    expect(proxyProblem('http://h?q=1')).toMatch(/address and port/)
    expect(proxyProblem('127.0.0.1:8080')).not.toBeNull()
  })
})
