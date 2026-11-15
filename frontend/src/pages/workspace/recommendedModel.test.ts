import { describe, expect, it } from 'vitest'

import { recommendedModelNote } from './recommendedModel'
import { titleKind } from './titleKind'

describe('recommendedModelNote', () => {
  it('names a recommendation for every kind of title', () => {
    for (const kind of ['comic', 'video', 'audio', 'subtitle', 'novel'] as const) {
      expect(recommendedModelNote(kind)).toMatch(/^Recommended for /)
    }
  })
  it('gives a novel Luna and a comic Sonnet', () => {
    expect(recommendedModelNote('novel')).toContain('GPT-6 Luna')
    expect(recommendedModelNote('comic')).toContain('Claude Sonnet 5.5')
    expect(recommendedModelNote(titleKind('manga'))).toContain('comics')
  })
})
