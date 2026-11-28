import { describe, expect, it } from 'vitest'

import { NO_ZOOM } from './comicLogic'
import { arriveAt, panelIndexFor, panelScroll, panelStep, panelZoom, PANEL_START, visiblePart } from './panelLogic'

describe('panel stepping', () => {
  it('starts a page on its first panel', () => {
    expect(panelIndexFor(PANEL_START, 3, 4)).toBe(0)
  })

  it('shows the whole page when it has fewer than two panels', () => {
    expect(panelIndexFor(PANEL_START, 3, 0)).toBeNull()
    expect(panelIndexFor(PANEL_START, 3, 1)).toBeNull()
  })

  it('moves through the panels, then leaves the page forward', () => {
    let at = panelStep(3, 0, 3, 1)
    expect(at).toEqual({ page: 3, index: 1, leaving: 0 })
    at = panelStep(3, 2, 3, 1)
    expect(at.leaving).toBe(1)
    // Still on the page while it turns: no panel is forced back on.
    expect(panelIndexFor(at, 3, 3)).toBeNull()
    // The next page opens on its first panel.
    expect(panelIndexFor(arriveAt(at, 4), 4, 5)).toBe(0)
  })

  it('going back from the first panel opens the earlier page on its last panel', () => {
    const at = panelStep(4, 0, 3, -1)
    expect(at.leaving).toBe(-1)
    expect(panelIndexFor(arriveAt(at, 3), 3, 5)).toBe(4)
  })

  it('a backward jump after a back step opens on the first panel', () => {
    // Back off page 5's first panel lands on page 4's last; Home from there opens page 1 on its first.
    const landed = arriveAt(panelStep(5, 0, 3, -1), 4)
    expect(panelIndexFor(landed, 4, 3)).toBe(2)
    expect(panelIndexFor(arriveAt(landed, 1), 1, 3)).toBe(0)
  })

  it('a jump forward after going back opens on the first panel', () => {
    expect(panelIndexFor(arriveAt(panelStep(4, 0, 3, -1), 9), 9, 5)).toBe(0)
  })

  it('coming back to a page that was being left shows its first panel, not the whole page', () => {
    const onNext = arriveAt(panelStep(3, 2, 3, 1), 4)
    expect(panelIndexFor(arriveAt(onNext, 3), 3, 3)).toBe(0)
  })

  it('keeps an index that the page no longer has inside its panels', () => {
    expect(panelIndexFor({ page: 2, index: 7, leaving: 0 }, 2, 3)).toBe(2)
  })
})

describe('panel zoom', () => {
  const box = { w: 400, h: 600 }
  const view = { x: 0, y: 0, w: 400, h: 600 }
  const image = { x: 0, y: 0, w: 400, h: 600 }

  it('fills the view with the panel, centred', () => {
    const z = panelZoom({ x: 0.5, y: 0, w: 0.5, h: 0.5 }, image, view, box)
    // The panel is 200 x 300 on screen: it can grow 2x before it reaches the edges.
    expect(z.scale).toBeCloseTo(1.92)
    const left = z.x + z.scale * 200
    const right = left + z.scale * 200
    expect(left).toBeGreaterThanOrEqual(-1e-6)
    expect(right).toBeLessThanOrEqual(400 + 1e-6)
  })

  it('leaves a panel the size of the page unzoomed', () => {
    expect(panelZoom({ x: 0, y: 0, w: 1, h: 1 }, image, view, box)).toEqual(NO_ZOOM)
  })

  it('caps the zoom for a tiny panel', () => {
    expect(panelZoom({ x: 0.4, y: 0.4, w: 0.05, h: 0.05 }, image, view, box).scale).toBe(4)
  })

  it('ignores an empty panel or view', () => {
    expect(panelZoom({ x: 0, y: 0, w: 0, h: 0.5 }, image, view, box)).toEqual(NO_ZOOM)
    expect(panelZoom({ x: 0, y: 0, w: 0.5, h: 0.5 }, image, { ...view, h: 0 }, box)).toEqual(NO_ZOOM)
  })

  it('keeps only the part of the stage that is in the window', () => {
    expect(visiblePart({ x: 0, y: -100, w: 400, h: 1000 }, { w: 400, h: 700 })).toEqual({ x: 0, y: 100, w: 400, h: 700 })
  })
})

describe('panelScroll', () => {
  it('centres a panel that fits below the bars', () => {
    // 800 tall window, 100 of bars: 700 of room; a 300 tall panel at 1500 is centred 200 below the bars.
    expect(panelScroll(1500, 300, 800, 100)).toBe(1500 - 100 - 200)
  })
  it('puts a panel taller than the room just below the bars', () => {
    expect(panelScroll(1500, 900, 800, 100)).toBe(1400)
    expect(panelScroll(-400, 900, 800, 100)).toBe(-500)
  })
  it('leaves a panel already centred where it is', () => {
    expect(panelScroll(300, 300, 800, 100)).toBe(0)
  })
})
