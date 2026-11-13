import { describe, expect, it } from 'vitest'

import { copyResultText, titleTermsSummary } from './glossaryTitleTermsModel'

const t = (original: string, clash: boolean) => ({ term_original: original, term_translation: 'x', clashes_with_series: clash })

describe('titleTermsSummary', () => {
  it('says the title terms win, and nothing about clashes when there are none', () => {
    const text = titleTermsSummary([t('a', false), t('b', false)])
    expect(text).toContain('2 terms of its own')
    expect(text).not.toContain("can't be edited")
  })
  it('explains that a term sharing its original text with a series term cannot be edited here', () => {
    expect(titleTermsSummary([t('a', true)])).toContain("1 term of its own")
    expect(titleTermsSummary([t('a', true)])).toContain("it can't be edited or removed here")
    expect(titleTermsSummary([t('a', true), t('b', true)])).toContain("they can't be edited or removed here")
  })
})

describe('copyResultText', () => {
  it('reports copied and already-there terms', () => {
    expect(copyResultText({ copied: ['a'], skipped: [] })).toBe('1 term copied to the series.')
    expect(copyResultText({ copied: ['a', 'b'], skipped: ['c'] })).toBe('2 terms copied to the series. 1 already in the series, left as they are.')
  })
})
