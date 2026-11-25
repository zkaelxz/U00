import { describe, expect, it } from 'vitest'

import { activityDuration, barFractions, gpuHours } from './activityFormat'

describe('activityFormat', () => {
  it('words a duration', () => {
    expect(activityDuration(0.2)).toBe('1 s')
    expect(activityDuration(42)).toBe('42 s')
    expect(activityDuration(420)).toBe('7 min')
    expect(activityDuration(7500)).toBe('2 h 5 min')
    expect(activityDuration(7200)).toBe('2 h')
  })
  it('words GPU hours', () => {
    expect(gpuHours(0)).toBe('0 h')
    expect(gpuHours(60)).toBe('<0.1 h')
    expect(gpuHours(5400)).toBe('1.5 h')
  })
  it('scales bars to the busiest day', () => {
    expect(barFractions([0, 50, 100])).toEqual([0, 0.5, 1])
    expect(barFractions([0, 0])).toEqual([0, 0])
  })
})
