import { describe, expect, it } from 'vitest'

import { continueItems, countDramas, parseTime, readHref, shownTags, tileText } from './libraryView'

const t = (title_en: string | null, title_zh: string | null) => ({ title_en, title_zh })

describe('tileText', () => {
  it('prefers the first CJK character of the original title', () => {
    expect(tileText(t('Signal', '시그널'))).toBe('시')
    expect(tileText(t('Moonlit', 'Vol. 2 月光'))).toBe('月')
  })
  it('falls back to English initials, then "?"', () => {
    expect(tileText(t("heaven official's blessing", null))).toBe('HO')
    expect(tileText(t(null, null))).toBe('?')
  })
})

describe('shownTags', () => {
  it('caps the tags and counts the rest', () => {
    expect(shownTags(['a', 'b', 'c'])).toEqual({ shown: ['a', 'b'], more: 1 })
    expect(shownTags([])).toEqual({ shown: [], more: 0 })
  })
})

describe('parseTime', () => {
  it('reads naive timestamps as UTC and tolerates junk', () => {
    expect(parseTime('2026-09-29T10:00:00')).toBe(Date.UTC(2026, 8, 29, 10))
    expect(parseTime('2026-09-29T10:00:00+00:00')).toBe(Date.UTC(2026, 8, 29, 10))
    expect(parseTime(null)).toBe(0)
    expect(parseTime('nope')).toBe(0)
  })
})

describe('continueItems', () => {
  const read = (drama_id: number, accessed_at: string, percent_complete: number | null = 40) =>
    ({ drama_id, accessed_at, percent_complete, title_en: `D${drama_id}`, title_zh: null })
  const work = (id: number, updated_at: string) =>
    ({ id, updated_at, status: 'aligned', media_type: null, title_en: `D${id}`, title_zh: null })

  it('merges reading and workspace activity newest first, one entry per drama', () => {
    const items = continueItems(
      [read(1, '2026-09-29T12:00:00'), read(1, '2026-09-29T11:00:00'), read(2, '2026-09-28T09:00:00')],
      [work(2, '2026-09-29T10:00:00'), work(3, '2026-09-27T10:00:00')],
    )
    expect(items.map((x) => `${x.kind}-${x.dramaId}`)).toEqual(['read-1', 'work-2', 'work-3'])
  })

  it('is empty when there is nothing to resume', () => {
    expect(continueItems([], [])).toEqual([])
  })
})

describe('small helpers', () => {
  it('counts dramas in plain English', () => {
    expect(countDramas(1)).toBe('1 drama')
    expect(countDramas(3)).toBe('3 dramas')
  })
  it('sends comics to the comic reader', () => {
    expect(readHref({ id: 4, media_type: 'manga' })).toBe('#/comic/4')
    expect(readHref({ id: 4, media_type: 'novel' })).toBe('#/read/4')
  })
})
