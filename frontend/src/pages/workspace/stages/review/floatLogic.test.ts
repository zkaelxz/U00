import { describe, expect, it } from 'vitest'

import { clampFloatPos, clampFloatWidth, FLOAT_DEFAULT_WIDTH, FLOAT_MIN_WIDTH } from './floatLogic'

describe('clampFloatWidth', () => {
  it('keeps a width inside the limits', () => {
    expect(clampFloatWidth(100, 1400)).toBe(FLOAT_MIN_WIDTH)
    expect(clampFloatWidth(5000, 1400)).toBe(960)
    expect(clampFloatWidth(500, 1400)).toBe(500)
  })
  it('never exceeds a phone screen', () => {
    expect(clampFloatWidth(900, 390)).toBe(374)
  })
  it('falls back for a stored value that is not a number', () => {
    expect(clampFloatWidth(NaN, 1400)).toBe(FLOAT_DEFAULT_WIDTH)
  })
})

describe('clampFloatPos', () => {
  it('keeps the box on screen', () => {
    const size = { width: 300, height: 200 }
    const viewport = { width: 1000, height: 600 }
    expect(clampFloatPos({ right: -5, bottom: -5 }, size, viewport)).toEqual({ right: 0, bottom: 0 })
    expect(clampFloatPos({ right: 9999, bottom: 9999 }, size, viewport)).toEqual({ right: 700, bottom: 400 })
  })
})
