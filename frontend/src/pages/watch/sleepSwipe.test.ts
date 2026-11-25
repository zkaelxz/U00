import { describe, expect, it } from 'vitest'

import { sleepLeftText, sleepRemainingMs } from './sleepLogic'
import { swipeAction } from './swipeLogic'

describe('sleep timer', () => {
  it('counts down and never goes below zero', () => {
    expect(sleepRemainingMs(10_000, 4_000)).toBe(6_000)
    expect(sleepRemainingMs(10_000, 20_000)).toBe(0)
  })
  it('words the time left', () => {
    expect(sleepLeftText(12 * 60000 - 5)).toBe('12 min left')
    expect(sleepLeftText(30_000)).toBe('Under a minute left')
  })
})

describe('swipeAction', () => {
  it('ignores taps, wobbles and mostly vertical drags', () => {
    expect(swipeAction(5, 3, 400)).toBeNull()
    expect(swipeAction(80, 70, 400)).toBeNull()
  })
  it('seeks forward on a left swipe and back on a right swipe', () => {
    expect(swipeAction(-100, 5, 400)).toEqual({ kind: 'seek', by: 10 })
    expect(swipeAction(100, 5, 400)).toEqual({ kind: 'seek', by: -10 })
  })
  it('goes to the next episode on a long left swipe only', () => {
    expect(swipeAction(-250, 10, 400)).toEqual({ kind: 'next' })
    expect(swipeAction(250, 10, 400)).toEqual({ kind: 'seek', by: -10 })
  })
})
