import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createRailOpen, RAIL_CLOSE_DELAY_MS } from './railOpen'

describe('floating dock open state', () => {
  let open: boolean
  let rail: ReturnType<typeof createRailOpen>
  beforeEach(() => {
    vi.useFakeTimers()
    open = false
    rail = createRailOpen((v) => { open = v })
  })
  afterEach(() => {
    rail.dispose()
    vi.useRealTimers()
  })

  it('opens when pointed at and collapses shortly after the pointer leaves, even after a mouse click inside', () => {
    rail.pointerEnter()
    expect(open).toBe(true)
    rail.focusIn(false) // a mouse click focuses the link, but not as keyboard focus
    rail.pointerLeave()
    expect(open).toBe(true)
    vi.advanceTimersByTime(RAIL_CLOSE_DELAY_MS)
    expect(open).toBe(false)
  })

  it('stays open when the pointer comes back before the delay ends', () => {
    rail.pointerEnter()
    rail.pointerLeave()
    vi.advanceTimersByTime(RAIL_CLOSE_DELAY_MS - 50)
    rail.pointerEnter()
    vi.advanceTimersByTime(RAIL_CLOSE_DELAY_MS)
    expect(open).toBe(true)
  })

  it('keeps open while keyboard focus is inside, and closes when it leaves', () => {
    rail.pointerEnter()
    rail.focusIn(true)
    rail.pointerLeave()
    vi.advanceTimersByTime(RAIL_CLOSE_DELAY_MS * 2)
    expect(open).toBe(true)
    rail.focusOut()
    expect(open).toBe(false)
  })

  it('opens for keyboard focus alone', () => {
    rail.focusIn(true)
    expect(open).toBe(true)
  })

  it('closes at once when the page is pressed outside it', () => {
    rail.pointerEnter()
    rail.focusIn(true)
    rail.pressOutside()
    expect(open).toBe(false)
  })
})
