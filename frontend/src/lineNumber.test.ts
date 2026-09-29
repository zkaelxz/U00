import { describe, expect, it } from 'vitest'

import { idxFromLineNumber, lineNumber } from './lineNumber'

describe('line numbers', () => {
  it('shows the 0-based idx as a 1-based number and back', () => {
    expect(lineNumber(0)).toBe(1)
    expect(lineNumber(41)).toBe(42)
    expect(idxFromLineNumber(1)).toBe(0)
    expect(idxFromLineNumber(lineNumber(7))).toBe(7)
  })
})
