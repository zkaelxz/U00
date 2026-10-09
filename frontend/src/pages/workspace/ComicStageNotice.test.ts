import { describe, expect, it } from 'vitest'

import { isLineStageForComic } from './ComicStageNotice'

describe('isLineStageForComic', () => {
  it('covers the line stages and leaves Source and no stage alone', () => {
    for (const s of ['translate', 'review', 'dub', 'export'] as const) expect(isLineStageForComic(s)).toBe(true)
    expect(isLineStageForComic('source')).toBe(false)
    expect(isLineStageForComic(null)).toBe(false)
  })
})
