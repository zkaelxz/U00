import { describe, expect, it } from 'vitest'

import { frameClock, frameOf, stepFrames } from './frameTimingLogic'

describe('frame timing', () => {
  const bounds = { min: 1, max: 5 }

  it('numbers frames from the time and formats the clock to the millisecond', () => {
    expect(frameOf(4, 24)).toBe(96)
    expect(frameOf(723.48, 25)).toBe(18087)
    expect(frameClock(723.48)).toBe('12:03.480')
    expect(frameClock(4)).toBe('0:04.000')
  })

  it('steps whole frames and stops at the edge bounds', () => {
    expect(stepFrames(4, 1, 24, bounds)).toBeCloseTo(97 / 24, 3)
    expect(stepFrames(4, -10, 24, bounds)).toBeCloseTo(86 / 24, 3)
    expect(stepFrames(4.9, 10, 24, bounds)).toBe(5)
    expect(stepFrames(1.02, -10, 24, bounds)).toBe(1)
  })
})
