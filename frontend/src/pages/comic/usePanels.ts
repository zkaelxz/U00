// Panel-by-panel reading in the paged comic reader: fetches the current page's panels and zooms to one at a time.
import { useCallback, useEffect, useMemo, useState, type MutableRefObject, type RefObject } from 'react'

import { comicApi } from '../../api/comic'
import { usePersistedState } from '../../hooks/usePersistedState'
import type { ComicPageInfo, ComicPanel } from '../../types/comic'
import { isStripPage, NO_ZOOM, type Zoom } from './comicLogic'
import { panelIndexFor, panelStep, panelZoom, PANEL_START, untransformed, visiblePart, type PanelAt } from './panelLogic'

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
  zoom: Zoom
  zoomTo: (z: Zoom) => void
}

const rectOf = (el: Element) => {
  const r = el.getBoundingClientRect()
  return { x: r.left, y: r.top, w: r.width, h: r.height }
}

export function usePanels({ dramaId, single, rtl, page, current, stageRef, figures, zoom, zoomTo }: Options) {
  // Kept per browser for every title: it is how this reader likes to read, not a fact about the title.
  const [on, setOn] = usePersistedState('comic.panels', false)
  const active = on && single && dramaId !== undefined
  const [found, setFound] = useState<Record<string, ComicPanel[]>>({})
  const [at, setAt] = useState<PanelAt>(PANEL_START)
  const key = page ? `${page.id}:${rtl}` : ''
  const wanted = active && !!page && !isStripPage(page)
  const panels = wanted ? found[key] : undefined

  useEffect(() => {
    if (!wanted || !page || dramaId === undefined || found[key] !== undefined) return
    let cancelled = false
    // A page the server can't split (or a failed request) reads as a whole page.
    comicApi.panels(dramaId, page.id, rtl).then(
      (r) => !cancelled && setFound((f) => ({ ...f, [key]: r.panels })),
      () => !cancelled && setFound((f) => ({ ...f, [key]: [] })),
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
      const s = rectOf(stage)
      const image = untransformed(rectOf(img), s, zoom)
      if (image.w <= 0 || image.h <= 0) return
      const box = { w: stage.clientWidth || s.w, h: stage.clientHeight || s.h }
      zoomTo(panelZoom(panel, image, visiblePart(s, { w: window.innerWidth, h: window.innerHeight }), box))
    }
    if (img.getBoundingClientRect().height > 0) apply()
    else img.addEventListener('load', apply, { once: true })
    window.addEventListener('resize', apply)
    return () => {
      img.removeEventListener('load', apply)
      window.removeEventListener('resize', apply)
    }
    // The zoom is read, not followed: zooming by hand must not snap back to the panel.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [panel, current, figures, stageRef, zoomTo])

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
              setOn(next)
              if (!next) zoomTo(NO_ZOOM)
            },
          },
    [dramaId, on, setOn, zoomTo],
  )
  return { step, control }
}
