// Panel-by-panel reading: which panel a tap moves to, and the zoom that fills the screen with it. No React here.
import type { ComicPanel } from '../../types/comic'
import { clampZoom, NO_ZOOM, ZOOM, type Zoom } from './comicLogic'

export interface Rect {
  x: number
  y: number
  w: number
  h: number
}

/**
 * Where the reader is: panel `index` of page `page` (LAST_PANEL: its last one), or leaving it (`leaving` 1 or -1)
 * for the page beside it.
 */
export interface PanelAt {
  page: number
  index: number
  leaving: 0 | 1 | -1
}

export const PANEL_START: PanelAt = { page: 0, index: 0, leaving: 0 }
export const LAST_PANEL = -1

// A little of the page stays around the panel, so its border shows.
const FILL = 0.96

/**
 * The panel shown on page `current`, which has `count` panels, or null when none is (fewer than two panels: the
 * whole page shows; or this page is being left). A page `at` does not name opens on its first panel.
 */
export function panelIndexFor(at: PanelAt, current: number, count: number): number | null {
  if (count < 2) return null
  if (at.page !== current) return 0
  if (at.leaving) return null
  return at.index === LAST_PANEL ? count - 1 : Math.min(Math.max(at.index, 0), count - 1)
}

/**
 * The reader is now on page `current`. Reached by stepping back off the first panel of a later page, it opens on
 * its last panel, so going back reads the page backwards; reached any other way, on its first.
 */
export function arriveAt(at: PanelAt, current: number): PanelAt {
  return { page: current, index: at.leaving === -1 && current < at.page ? LAST_PANEL : 0, leaving: 0 }
}

/** One step from panel `index` of `count`: the next panel on this page, or off the page (`leaving`). */
export function panelStep(page: number, index: number, count: number, dir: 1 | -1): PanelAt {
  const next = index + dir
  return next >= 0 && next < count ? { page, index: next, leaving: 0 } : { page, index, leaving: dir }
}

/** The part of a stage at `stage` (screen coordinates, w x h) that is inside a window of `win`, in stage coordinates. */
export function visiblePart(stage: Rect, win: { w: number; h: number }): Rect {
  const x = Math.max(0, -stage.x)
  const y = Math.max(0, -stage.y)
  return { x, y, w: Math.max(0, Math.min(stage.w, win.w - stage.x) - x), h: Math.max(0, Math.min(stage.h, win.h - stage.y) - y) }
}

/**
 * The zoom (as useZoom applies it to the stage) that shows `panel` of the page image at `image` as large as fits in
 * `view`, centred. All rectangles are in unzoomed stage coordinates; `box` is the stage's size.
 */
export function panelZoom(panel: ComicPanel, image: Rect, view: Rect, box: { w: number; h: number }): Zoom {
  const p = { x: image.x + panel.x * image.w, y: image.y + panel.y * image.h, w: panel.w * image.w, h: panel.h * image.h }
  if (p.w <= 0 || p.h <= 0 || view.w <= 0 || view.h <= 0) return NO_ZOOM
  const scale = Math.min(ZOOM.max, Math.max(ZOOM.min, Math.min(view.w / p.w, view.h / p.h) * FILL))
  return clampZoom(
    { scale, x: view.x + (view.w - scale * p.w) / 2 - scale * p.x, y: view.y + (view.h - scale * p.h) / 2 - scale * p.y },
    box.w,
    box.h,
  )
}

/**
 * How far to scroll the window to bring a panel into view when no zoom can (a panel taller than the visible part,
 * as in Fit width): `top` and `h` are the panel's screen position and height, `inset` the bars covering the window
 * top. A panel already in full view stays put; one that fits is centred below the bars; a taller one starts just
 * below them.
 */
export function panelScroll(top: number, h: number, winH: number, inset: number): number {
  if (top >= inset && top + h <= winH) return 0
  const room = winH - inset
  return Math.round(h <= room ? top - inset - (room - h) / 2 : top - inset)
}
