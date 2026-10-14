import { describe, expect, it } from 'vitest'

import { STALL_NUDGE_S, STALL_REBUILD_S, errorAction, placement, playerSrc, stallAction } from './playerLogic'

describe('placement', () => {
  it('places the picture the wanted number of seconds behind the newest recorded second', () => {
    expect(placement(0, 100, 99, 30, null)).toEqual({ goal: 30, to: 70 })
    expect(placement(0, 100, 70, 30, null)).toEqual({ goal: 30, to: null })
    expect(placement(0, 100, 72, 30, 30)).toEqual({ goal: 30, to: null })
  })
  it('with less recorded than wanted, starts at the oldest second and then stays put as the recording grows', () => {
    expect(placement(0, 32, 20, 40, null)).toEqual({ goal: 32, to: 0 })
    // Two seconds on, the playhead and the newest second have both moved two seconds.
    expect(placement(0, 34, 2, 40, 32)).toEqual({ goal: 32, to: null })
    expect(placement(0, 60, 28, 40, 32)).toEqual({ goal: 32, to: null })
  })
  it('seeks back when playback has fallen behind its goal, and places again off the back of the window', () => {
    expect(placement(0, 100, 50, 30, 30)).toEqual({ goal: 30, to: 70 })
    expect(placement(40, 140, 20, 30, 30)).toEqual({ goal: 30, to: 110 })
  })
  it('a new delay (goal reset) moves the picture', () => {
    expect(placement(0, 100, 70, 10, null)).toEqual({ goal: 10, to: 90 })
  })
  it('does nothing without a measurable window', () => {
    expect(placement(0, 0, 0, 30, null)).toEqual({ goal: null, to: null })
    expect(placement(0, NaN, 0, 30, null).to).toBeNull()
    expect(placement(0, 100, NaN, 30, null).to).toBeNull()
  })
  it('a delay of 0 follows the live edge', () => {
    expect(placement(0, 100, 80, 0, null)).toEqual({ goal: 0, to: 100 })
  })
})

describe('errorAction', () => {
  it('acts only on fatal errors, by kind', () => {
    expect(errorAction(false, 'networkError')).toBe('ignore')
    expect(errorAction(true, 'networkError')).toBe('reload')
    expect(errorAction(true, 'mediaError')).toBe('recover-media')
    expect(errorAction(true, 'otherError')).toBe('rebuild')
  })
})

describe('stallAction', () => {
  it('nudges after a while and rebuilds when it does not come back', () => {
    expect(stallAction(0)).toBe('none')
    expect(stallAction(STALL_NUDGE_S - 1)).toBe('none')
    expect(stallAction(STALL_NUDGE_S)).toBe('nudge')
    expect(stallAction(STALL_REBUILD_S)).toBe('rebuild')
  })
})

describe('playerSrc', () => {
  it('points at the session\'s master playlist', () => {
    expect(playerSrc('live_abc')).toMatch(/\/api\/live\/sessions\/live_abc\/player\/master\.m3u8$/)
  })
})
