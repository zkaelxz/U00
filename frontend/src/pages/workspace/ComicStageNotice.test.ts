import { describe, expect, it } from 'vitest'

import { isLineStageForComic } from './ComicStageNotice'
import { isCastTab } from './stages'

describe('isLineStageForComic', () => {
  it('covers the line stages and leaves Source and no stage alone', () => {
    for (const s of ['translate', 'review', 'export'] as const) expect(isLineStageForComic(s)).toBe(true)
    expect(isLineStageForComic('source')).toBe(false)
    expect(isLineStageForComic(null)).toBe(false)
  })
})

describe('isCastTab', () => {
  it('lets the glossary and characters tabs through for a comic', () => {
    expect(isCastTab('glossary')).toBe(true)
    expect(isCastTab('characters')).toBe(true)
    expect(isCastTab('translate')).toBe(false)
    expect(isCastTab(undefined)).toBe(false)
  })
})
