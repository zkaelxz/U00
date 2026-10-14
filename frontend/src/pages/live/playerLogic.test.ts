import { describe, expect, it } from 'vitest'

import { STALL_NUDGE_S, STALL_REBUILD_S, errorAction, planSeek, playerSrc, stallAction } from './playerLogic'

describe('planSeek', () => {
  it('holds the picture the wanted number of seconds behind the newest recorded second', () => {
    expect(planSeek(0, 100, 99, 30)).toBe(70)
    expect(planSeek(0, 100, 70, 30)).toBeNull()
    expect(planSeek(0, 100, 72, 30)).toBeNull()
  })
  it('plays from the oldest second when less is recorded than asked, and then stays put', () => {
    expect(planSeek(10, 25, 24, 30)).toBe(10)
    expect(planSeek(10, 25, 11, 30)).toBeNull()
  })
  it('does nothing without a measurable window', () => {
    expect(planSeek(0, 0, 0, 30)).toBeNull()
    expect(planSeek(0, NaN, 0, 30)).toBeNull()
    expect(planSeek(0, 100, NaN, 30)).toBeNull()
  })
  it('a delay of 0 follows the live edge', () => {
    expect(planSeek(0, 100, 80, 0)).toBe(100)
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
