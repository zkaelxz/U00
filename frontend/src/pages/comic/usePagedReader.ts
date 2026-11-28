// What the comic reader and the saved-manga reader do the same way once they know the current page:
// tap thirds, zoom, keys, the chrome toggle, image problems, the page figures and the preload window.
import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from 'react'

import type { ImageProblem } from '../../api/comic'
import { usePreloadImages } from '../../hooks/usePreloadImages'
import { useShortcut } from '../../hooks/useShortcut'
import { setShellBarsHidden } from '../../phoneShell'
import type { ComicPageInfo } from '../../types/comic'
import { keyAction, preloadWindow, spreadIndex, spreadStep, tapAction, ZOOM, type ComicAction, type ComicPrefs } from './comicLogic'
import { usePanels } from './usePanels'
import { useZoom } from './useZoom'

interface Options {
  stageRef: RefObject<HTMLElement | null>
  pages: ComicPageInfo[]
  prefs: ComicPrefs
  current: number | null
  count: number
  go: (n: number) => void
  srcFor: (p: ComicPageInfo) => string
  // Two-page mode: what is shown together; stepping then moves a whole spread.
  spreads?: number[][] | null
  // The title whose stored pages can be read panel by panel; left out where pages have no panels.
  panelsFor?: number
}

export function usePagedReader({ stageRef, pages, prefs, current, count, go, srcFor, spreads = null, panelsFor }: Options) {
  const paged = prefs.mode === 'paged'
  const rtl = paged && prefs.rtl
  const [chrome, setChrome] = useState(true)
  // The reader's own bars are hidden by a class on the reader; the app's header and the phone app's
  // Studio | Grab strip sit outside it, so they follow the same switch and always come back on leaving.
  useEffect(() => {
    if (chrome) return undefined
    document.documentElement.classList.add('reader-immersive')
    setShellBarsHidden(true)
    return () => {
      document.documentElement.classList.remove('reader-immersive')
      setShellBarsHidden(false)
    }
  }, [chrome])
  const [forbidden, setForbidden] = useState(false)
  const figures = useRef(new Map<number, HTMLElement>())
  const figureRef = (n: number) => (el: HTMLElement | null) => {
    if (el) figures.current.set(n, el)
    else figures.current.delete(n)
  }

  const preload = useMemo(
    () => {
      if (current === null) return []
      // Two-page mode: the whole next and previous spread, not just the page beside the current one.
      const wanted = spreads
        ? [1, -1].flatMap((d) => spreads[spreadIndex(spreads, current) + d] ?? [])
        : preloadWindow(current, count, prefs.mode)
      return wanted.map((n) => pages[n - 1]).filter(Boolean).map(srcFor)
    },
    [current, count, prefs.mode, pages, srcFor, spreads],
  )
  // A refused image stops the preloading too, so nothing keeps asking.
  usePreloadImages(forbidden ? [] : preload)

  const onProblem = useCallback((kind: ImageProblem) => {
    if (kind === 'forbidden') setForbidden(true)
  }, [])

  const step = useCallback(
    (dir: 1 | -1) => {
      const from = current ?? 1
      go(spreads ? (spreadStep(spreads, from, dir) ?? from) : from + dir)
    },
    [spreads, current, go],
  )
  // Set once the panels are known (they need the zoom below): a tap or key goes to the next panel before the page.
  const panelTurn = useRef<(dir: 1 | -1) => boolean>(() => false)
  const advance = useCallback(
    (dir: 1 | -1) => {
      if (!panelTurn.current(dir)) step(dir)
    },
    [step],
  )

  const onTap = useCallback(
    (fraction: number) => {
      const a = tapAction(fraction, rtl)
      if (a === 'chrome') setChrome((v) => !v)
      else if (paged) advance(a === 'next' ? 1 : -1)
      else window.scrollBy({ top: (a === 'next' ? 0.85 : -0.85) * window.innerHeight })
    },
    [rtl, paged, advance],
  )
  const zoom = useZoom(stageRef, onTap, `${prefs.mode}:${paged ? current : ''}:${prefs.fit}`)
  const shownPage = current !== null ? (pages[current - 1] ?? null) : null
  const panels = usePanels({
    dramaId: panelsFor,
    single: paged && !spreads,
    rtl,
    page: shownPage,
    current,
    stageRef,
    figures,
    zoomTo: zoom.zoomTo,
    layoutKey: `${prefs.fit}:${shownPage ? srcFor(shownPage) : ''}`,
  })
  useEffect(() => {
    panelTurn.current = panels.step
  }, [panels.step])

  const act = (a: ComicAction) => {
    if (current === null) return
    if (a === 'next') advance(1)
    else if (a === 'prev') advance(-1)
    else if (a === 'first') go(1)
    else if (a === 'last') go(count)
    else if (a === 'zoomIn') zoom.zoomBy(ZOOM.step)
    else if (a === 'zoomOut') zoom.zoomBy(1 / ZOOM.step)
    else zoom.reset()
  }
  useShortcut((combo, { inText }) => {
    if (inText || current === null || document.querySelector('dialog[open]')) return false
    const a = keyAction(combo, prefs.mode, rtl)
    if (!a) return false
    act(a)
    return true
  })

  return { paged, rtl, chrome, forbidden, figures, figureRef, onProblem, zoom, act, step, panels: panels.control }
}

export function readerClasses(phone: boolean, chrome: boolean, mode: ComicPrefs['mode']): string {
  return ['comic', 'reader', phone ? 'comic-phone reader-phone' : '', chrome ? '' : 'comic-chrome-off', `comic-mode-${mode}`].filter(Boolean).join(' ')
}
