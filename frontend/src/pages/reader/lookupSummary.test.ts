import { describe, expect, it } from 'vitest'

import { lookupSummary } from './lookupSummary'

describe('lookupSummary', () => {
  it('counts what was saved', () => {
    expect(lookupSummary(1, 'zh', false)).toBe('Saved 1 definition.')
    expect(lookupSummary(7, 'ja', true)).toBe('Saved 7 definitions.')
    expect(lookupSummary(0, 'zh', false)).toBe('Saved 0 definitions.')
  })
  it('says why Japanese and Korean find nothing without AI', () => {
    for (const lang of ['ja', 'ko']) expect(lookupSummary(0, lang, false)).toContain('Chinese only')
    expect(lookupSummary(0, 'ja', true)).toBe('Saved 0 definitions.')
  })
})
