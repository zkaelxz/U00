import { describe, expect, it } from 'vitest'

import { labelFits } from './waveLabel'

describe('labelFits', () => {
  it('hides a label that cannot sit on one line, so it never wraps or clips to "#4"', () => {
    expect(labelFits('#40', 12)).toBe(false)
    expect(labelFits('#40', 28)).toBe(false)
    expect(labelFits('#40', 30)).toBe(true)
    expect(labelFits('#4', 22)).toBe(true)
  })

  it('needs more room for a longer label', () => {
    expect(labelFits('No line here: speech detected', 150)).toBe(false)
    expect(labelFits('No line here: speech detected', 220)).toBe(true)
  })

  it('treats an unknown width as not fitting', () => {
    expect(labelFits('#1', Number.NaN)).toBe(false)
  })
})
