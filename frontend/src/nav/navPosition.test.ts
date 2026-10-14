import { describe, expect, it } from 'vitest'

import { NAV_DOCK_KEY, NAV_POSITION_KEY, parseNavDock, parseNavPosition } from './navDock'

describe('nav placement values', () => {
  it('falls back to the left for anything but the three other sides', () => {
    expect(parseNavPosition(null)).toBe('left')
    expect(parseNavPosition('middle')).toBe('left')
    expect(['right', 'top', 'bottom'].map(parseNavPosition)).toEqual(['right', 'top', 'bottom'])
  })

  it('keeps the dock state and the position under separate keys', () => {
    expect(NAV_POSITION_KEY).not.toBe(NAV_DOCK_KEY)
    expect(parseNavDock('minimized')).toBe('minimized')
    expect(parseNavDock('top')).toBe('float')
  })
})
