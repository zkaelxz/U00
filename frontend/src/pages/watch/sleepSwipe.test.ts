import { describe, expect, it } from 'vitest'

import { sleepLeftText, sleepRemainingMs } from './sleepLogic'
import { startsAtEdge, swipeSeek } from './swipeLogic'

describe('sleep timer', () => {
  it('counts down and never goes below zero', () => {
    expect(sleepRemainingMs(10_000, 4_000)).toBe(6_000)
    expect(sleepRemainingMs(10_000, 20_000)).toBe(0)
  })
  it('words the time left', () => {
    expect(sleepLeftText(12 * 60000 - 5)).toBe('12 min')
    expect(sleepLeftText(30_000)).toBe('under a minute')
  })
})

describe('swipeSeek', () => {
  it('ignores taps, wobbles and mostly vertical drags', () => {
    expect(swipeSeek(5, 3)).toBeNull()
    expect(swipeSeek(80, 70)).toBeNull()
  })
  it('seeks forward on a right swipe, like the seek bar, and back on a left swipe', () => {
    expect(swipeSeek(100, 5)).toBe(10)
    expect(swipeSeek(-100, 5)).toBe(-10)
  })
  it('leaves the screen edges to the phone', () => {
    expect(startsAtEdge(10, 390)).toBe(true)
    expect(startsAtEdge(380, 390)).toBe(true)
    expect(startsAtEdge(200, 390)).toBe(false)
  })
})
