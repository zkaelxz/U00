import { describe, expect, it } from 'vitest'

import { formatDefault, formatRange, rangeHint } from './formatRange'

describe('formatRange', () => {
  it('shows both ends with the same decimals, a leading zero and the unit', () => {
    expect(formatRange(0.1, 2, 's')).toBe('0.1-2.0 s')
    expect(formatRange(0.1, 0.9)).toBe('0.1-0.9')
    expect(formatRange(0.5, 10, 's')).toBe('0.5-10.0 s')
  })

  it('writes whole numbers without decimals', () => {
    expect(formatRange(100, 3000, 'ms')).toBe('100-3000 ms')
    expect(formatRange(1, 10)).toBe('1-10')
    expect(formatRange(0, 20)).toBe('0-20')
  })

  it('never drops the zero before the point', () => {
    expect(formatRange(0.1, 2)).not.toMatch(/(^|[^0-9])\.\d/)
  })
})

describe('formatDefault and rangeHint', () => {
  it('names a default with its own decimals', () => {
    expect(formatDefault(0.35, 's')).toBe('default 0.35 s')
    expect(formatDefault(300, 'ms')).toBe('default 300 ms')
    expect(formatDefault(5)).toBe('default 5')
  })

  it('joins the range and the default', () => {
    expect(rangeHint(0.1, 2, { unit: 's', default: 0.35 })).toBe('0.1-2.0 s, default 0.35 s.')
    expect(rangeHint(1, 10, { default: 5 })).toBe('1-10, default 5.')
    expect(rangeHint(1, 20)).toBe('1-20.')
  })
})
