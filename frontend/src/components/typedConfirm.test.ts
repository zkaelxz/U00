import { describe, expect, it } from 'vitest'

import { typedMatches } from './typedConfirm'

describe('typedMatches', () => {
  it('needs the exact word, ignoring spaces and case', () => {
    expect(typedMatches('resegment', 'resegment')).toBe(true)
    expect(typedMatches(' Restore ', 'restore')).toBe(true)
    expect(typedMatches('restor', 'restore')).toBe(false)
    expect(typedMatches('', 'restore')).toBe(false)
  })

  it('exact mode also needs the letter case', () => {
    expect(typedMatches('DELETE', 'DELETE', true)).toBe(true)
    expect(typedMatches(' DELETE ', 'DELETE', true)).toBe(true)
    expect(typedMatches('delete', 'DELETE', true)).toBe(false)
    expect(typedMatches('Delete', 'DELETE', true)).toBe(false)
  })
})
