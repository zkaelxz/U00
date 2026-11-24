import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { LONG_PRESS_MS, longPress, pageFileName } from './pageActionsLogic'

describe('longPress', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('fires once after a still hold', () => {
    const fire = vi.fn()
    const press = longPress()
    press.down({ x: 10, y: 10 }, 1, fire)
    press.move({ x: 14, y: 12 })
    vi.advanceTimersByTime(LONG_PRESS_MS - 1)
    expect(fire).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(fire).toHaveBeenCalledTimes(1)
  })

  it('does not fire for a scroll, a lifted finger or a pinch', () => {
    const fire = vi.fn()
    const press = longPress()
    press.down({ x: 10, y: 10 }, 1, fire)
    press.move({ x: 10, y: 40 })
    vi.advanceTimersByTime(LONG_PRESS_MS)
    press.down({ x: 10, y: 10 }, 1, fire)
    press.cancel()
    vi.advanceTimersByTime(LONG_PRESS_MS)
    press.down({ x: 10, y: 10 }, 1, fire)
    press.down({ x: 90, y: 90 }, 2, fire)
    vi.advanceTimersByTime(LONG_PRESS_MS)
    expect(fire).not.toHaveBeenCalled()
  })
})

describe('pageFileName', () => {
  it('pads the page number and keeps the picture type', () => {
    expect(pageFileName('Night Rain', 12, 'image/png')).toBe('Night Rain - page 012.png')
    expect(pageFileName('夜雨', 3, 'image/webp; q=1')).toBe('夜雨 - page 003.webp')
  })
  it('drops characters a file name cannot hold and falls back when nothing is left', () => {
    expect(pageFileName('A/B: "C"?', 1, 'image/jpeg')).toBe('A B C - page 001.jpg')
    expect(pageFileName('  ', 1, 'application/octet-stream')).toBe('Comic - page 001.jpg')
  })
})
