import { describe, expect, it } from 'vitest'

import { typedMatches } from './typedConfirm'

describe('typedMatches', () => {
  it('needs the exact word, ignoring spaces and case', () => {
    expect(typedMatches('resegment', 'resegment')).toBe(true)
    expect(typedMatches(' Restore ', 'restore')).toBe(true)
    expect(typedMatches('restor', 'restore')).toBe(false)
    expect(typedMatches('', 'restore')).toBe(false)
  })
})
