import { describe, expect, it } from 'vitest'

import { isMetered, lowDataActive } from './lowData'

describe('low data mode', () => {
  it('Auto follows the data-saver switch or a cellular connection', () => {
    expect(isMetered({ connection: { saveData: true } })).toBe(true)
    expect(isMetered({ connection: { type: 'cellular' } })).toBe(true)
    expect(isMetered({ connection: { type: 'wifi', saveData: false } })).toBe(false)
    expect(isMetered({})).toBe(false)
    expect(isMetered(undefined)).toBe(false)
  })

  it('On and Off ignore the connection', () => {
    expect(lowDataActive('on', false)).toBe(true)
    expect(lowDataActive('off', true)).toBe(false)
    expect(lowDataActive('auto', true)).toBe(true)
    expect(lowDataActive('auto', false)).toBe(false)
  })
})
