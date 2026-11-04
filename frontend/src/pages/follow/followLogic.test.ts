import { describe, expect, it } from 'vitest'

import { formatClock, nudge, pause, positionAt, seekTo, start, STOPPED_AT_START } from './followLogic'

describe('follow clock', () => {
  it('stays put while paused', () => {
    expect(positionAt(STOPPED_AT_START, 99_000)).toBe(0)
  })

  it('counts real time once started', () => {
    const c = start(STOPPED_AT_START, 10_000)
    expect(positionAt(c, 17_500)).toBeCloseTo(7.5)
  })

  it('keeps the position on pause and resumes from it', () => {
    const paused = pause(start(STOPPED_AT_START, 1_000), 6_000)
    expect(paused).toMatchObject({ running: false, base: 5 })
    expect(positionAt(start(paused, 50_000), 52_000)).toBeCloseTo(7)
  })

  it('nudges a running clock without stopping it, and never below zero', () => {
    const c = start(STOPPED_AT_START, 0)
    const back = nudge(c, -0.5, 3_000)
    expect(back.running).toBe(true)
    expect(positionAt(back, 3_000)).toBeCloseTo(2.5)
    expect(positionAt(nudge(STOPPED_AT_START, -9, 0), 0)).toBe(0)
  })

  it('jumps to a chosen line while paused or running', () => {
    expect(positionAt(seekTo(STOPPED_AT_START, 42, 0), 5_000)).toBe(42)
    expect(positionAt(seekTo(start(STOPPED_AT_START, 0), 42, 1_000), 3_000)).toBeCloseTo(44)
  })

  it('formats minutes and seconds', () => {
    expect(formatClock(0)).toBe('0:00')
    expect(formatClock(125.9)).toBe('2:05')
  })
})
