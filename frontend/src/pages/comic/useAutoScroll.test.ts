import { describe, expect, it } from 'vitest'

import { SCROLL_PX_PER_SECOND, SCROLL_SPEEDS } from './useAutoScroll'

describe('auto-scroll speeds', () => {
  it('lists Off first and gets faster down the list', () => {
    const speeds = SCROLL_SPEEDS.map(([v]) => SCROLL_PX_PER_SECOND[v])
    expect(SCROLL_SPEEDS[0][0]).toBe('off')
    expect(speeds[0]).toBe(0)
    expect([...speeds].sort((a, b) => a - b)).toEqual(speeds)
  })
})
