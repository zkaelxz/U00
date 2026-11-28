// Panel-by-panel reading in the paged comic reader: fetches the current page's panels and zooms to one at a time.
import { useCallback, useEffect, useMemo, useState, useSyncExternalStore, type MutableRefObject, type RefObject } from 'react'

import { comicApi } from '../../api/comic'
import { browserStorage, readPref, writePref } from '../../hooks/usePersistedState'
import type { ComicPageInfo, ComicPanel } from '../../types/comic'
import { isStripPage, NO_ZOOM, type Zoom } from './comicLogic'
import { arriveAt, panelIndexFor, panelScroll, panelStep, panelZoom, PANEL_START, visiblePart, type PanelAt, type Rect } from './panelLogic'

// The switch is kept per browser for every title (how this reader likes to read, not a fact about the title), and
// shared: the two-page layout reads it too, so one page shows at a time while it is on.
const SWITCH_KEY = 'comic.panels'
let switchOn: boolean | null = null
const switchListeners = new Set<() => void>()
const readSwitch = () => (switchOn ??= readPref(browserStorage(), SWITCH_KEY, false))
const subscribeSwitch = (l: () => void) => {
  switchListeners.add(l)
  return () => switchListeners.delete(l)
}
function setSwitch(next: boolean) {
  switchOn = next
  writePref(browserStorage(), SWITCH_KEY, next)
  switchListeners.forEach((l) => l())
}

export function usePanelSwitch(): boolean {
  return useSyncExternalStore(subscribeSwitch, readSwitch, () => false)
}

interface Options {
  /** The title whose pages have panels; undefined where a reader has none (saved manga). */
  dramaId: number | undefined
  /** One page at a time, not two side by side. */
  single: boolean
  rtl: boolean
  page: ComicPageInfo | null
  current: number | null
  stageRef: RefObject<HTMLElement | null>
  figures: MutableRefObject<Map<number, HTMLElement>>
  zoomTo: (z: Zoom) => void
  /** Changes when the page is laid out again without a resize (another fit, the typeset image). */
  layoutKey: string
}

// Layout offsets, which a CSS transform does not change, so a zoomed stage measures the same as an unzoomed one.
function layoutRect(el: HTMLElement, stage: HTMLElement): Rect | null {
  let x = 0
  let y = 0
  for (let at: Element | null = el; at !== stage; at = (at as HTMLElement).offsetParent) {
    if (!(at instanceof HTMLElement)) return null
    x += at.offsetLeft
    y += at.offsetTop
  }
  return { x, y, w: el.offsetWidth, h: el.offsetHeight }
}

export function usePanels({ dramaId, single, rtl, page, current, stageRef, figures, zoomTo, layoutKey }: Options) {
  const on = usePanelSwitch()
  const active = on && single && dramaId !== undefined
  const [found, setFound] = useState<Record<string, ComicPanel[]>>({})
  const [at, setAt] = useState<PanelAt>(PANEL_START)
  const [seen, setSeen] = useState(current)
  if (current !== seen) {
    setSeen(current)
    if (current !== null) setAt(arriveAt(at, current))
  }
  const key = page ? `${page.id}:${rtl}` : ''
  const wanted = active && !!page && !isStripPage(page)
  const panels = wanted ? found[key] : undefined

  useEffect(() => {
    if (!wanted || !page || dramaId === undefined || found[key] !== undefined) return
    let cancelled = false
    // A failed request is not kept: the page shows whole and is asked for again the next time it is shown.
    comicApi.panels(dramaId, page.id, rtl).then(
      (r) => !cancelled && setFound((f) => ({ ...f, [key]: r.panels })),
      () => undefined,
    )
    return () => {
      cancelled = true
    }
  }, [wanted, page, key, found, dramaId, rtl])

  const index = current !== null && panels ? panelIndexFor(at, current, panels.length) : null
  const panel = index !== null && panels ? panels[index] : null

  useEffect(() => {
    if (!panel || current === null) return undefined
    const img = figures.current.get(current)?.querySelector('img')
    const stage = stageRef.current
    if (!img || !stage) return undefined
    const apply = () => {
      const image = layoutRect(img, stage)
      if (!image || image.w <= 0 || image.h <= 0) return
      const r = stage.getBoundingClientRect()
      const box = { w: stage.clientWidth || r.width, h: stage.clientHeight || r.height }
      const view = visiblePart({ x: r.left, y: r.top, w: box.w, h: box.h }, { w: window.innerWidth, h: window.innerHeight })
      const zoom = panelZoom(panel, image, view, box)
      zoomTo(zoom)
      if (zoom.scale > 1) return
      // No zoom fits (a page taller than the window, as in Fit width): the window scrolls to the panel instead.
      const top = r.top + image.y + panel.y * image.h
      const h = panel.h * image.h
      const inset = parseFloat(getComputedStyle(stage).scrollMarginTop) || 0
      const by = panelScroll(top, h, window.innerHeight, inset)
      if (Math.abs(by) > 1) window.scrollBy({ top: by })
    }
    apply()
    // The stage changes size when the window does and when the bars hide; the image when it loads.
    if (typeof ResizeObserver === 'undefined') return undefined
    const watch = new ResizeObserver(apply)
    watch.observe(stage)
    watch.observe(img)
    return () => watch.disconnect()
  }, [panel, current, figures, stageRef, zoomTo, layoutKey])

  /** A tap or key toward `dir`: true when it moved to another panel, false when the page should turn. */
  const step = useCallback(
    (dir: 1 | -1): boolean => {
      if (!panels || current === null) return false
      // Still on a page it tried to leave (the last page has no next one): carry on from its panel.
      const from = index ?? (at.page === current && panels.length >= 2 ? at.index : null)
      if (from === null) return false
      const next = panelStep(current, from, panels.length, dir)
      setAt(next)
      return next.leaving === 0
    },
    [index, panels, current, at],
  )

  const control = useMemo(
    () =>
      dramaId === undefined
        ? undefined
        : {
            on,
            onChange: (next: boolean) => {
              setSwitch(next)
              if (!next) zoomTo(NO_ZOOM)
            },
          },
    [dramaId, on, zoomTo],
  )
  return { step, control }
}
