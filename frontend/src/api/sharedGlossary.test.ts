import { describe, expect, it } from 'vitest'

import { originLabel } from './sharedGlossary'

describe('originLabel', () => {
  it('names the group for an inherited term and the series otherwise', () => {
    expect(originLabel({ origin: 'shared', origin_name: 'Streamer' })).toBe('Shared: Streamer')
    expect(originLabel({ origin: 'series', origin_name: null })).toBe('This series')
  })
})
