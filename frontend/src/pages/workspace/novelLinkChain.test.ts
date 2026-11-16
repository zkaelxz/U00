import { describe, expect, it } from 'vitest'

import { mainButton } from './novelLinkChain'

describe('mainButton', () => {
  it('leads with Translate while lines lack English, Review once all have it, neither with no lines', () => {
    expect(mainButton(null, 0)).toBeNull()
    expect(mainButton(0, 0)).toBeNull()
    expect(mainButton(10, 4)).toBe('translate')
    expect(mainButton(10, 0)).toBe('review')
  })
})
