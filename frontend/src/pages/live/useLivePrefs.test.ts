import { describe, expect, it } from 'vitest'

import { withoutStaleChunkPrefs } from './useLivePrefs'

describe('withoutStaleChunkPrefs', () => {
  it('drops the old shared 10 s / 3 s pair so the language default applies', () => {
    expect(withoutStaleChunkPrefs({ segment_seconds: 10, overlap_seconds: 3, use_gpu: true })).toEqual({ use_gpu: true })
  })
  it('keeps any other chunk choice', () => {
    const prefs = { segment_seconds: 8, overlap_seconds: 3 }
    expect(withoutStaleChunkPrefs(prefs)).toBe(prefs)
  })
})
