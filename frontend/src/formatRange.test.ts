import { describe, expect, it } from 'vitest'

import { formatRange } from './formatRange'

describe('formatRange', () => {
  it('writes min-max with the unit after', () => {
    expect(formatRange(1, 60, 'lines')).toBe('1-60 lines')
  })
  it('keeps the same decimals on both ends, with the leading zero', () => {
    expect(formatRange(0.5, 2, 's', 1)).toBe('0.5-2.0 s')
    expect(formatRange(0, 1, '', 2)).toBe('0.00-1.00')
  })
})
