// What the comic reader and the saved-manga reader do the same way once they know the current page:
// tap thirds, zoom, keys, the chrome toggle, image problems, the page figures and the preload window.
import { useCallback, useMemo, useRef, useState, type RefObject } from 'react'

import type { ImageProblem } from '../../api/comic'
import { usePreloadImages } from '../../hooks/usePreloadImages'
import { useShortcut } from '../../hooks/useShortcut'
import type { ComicPageInfo } from '../../types/comic'
import { keyAction, preloadWindow, tapAction, ZOOM, type ComicAction, type ComicPrefs } from './comicLogic'
import { useZoom } from './useZoom'

interface Options {
  stageRef: RefObject<HTMLElement | null>
  pages: ComicPageInfo[]
  prefs: ComicPrefs
  current: number | null
  count: number
  go: (n: number) => void
  srcFor: (p: ComicPageInfo) => string
}

export function usePagedReader({ stageRef, pages, prefs, current, count, go, srcFor }: Options) {
  const paged = prefs.mode === 'paged'
  const rtl = paged && prefs.rtl
  const [chrome, setChrome] = useState(true)
  const [forbidden, setForbidden] = useState(false)
  const figures = useRef(new Map<number, HTMLElement>())
  const figureRef = (n: number) => (el: HTMLElement | null) => {
    if (el) figures.current.set(n, el)
    else figures.current.delete(n)
  }

  const preload = useMemo(
    () => (current === null ? [] : preloadWindow(current, count, prefs.mode).map((n) => pages[n - 1]).filter(Boolean).map(srcFor)),
    [current, count, prefs.mode, pages, srcFor],
  )
  // A refused image stops the preloading too, so nothing keeps asking.
  usePreloadImages(forbidden ? [] : preload)

  const onProblem = useCallback((kind: ImageProblem) => {
    if (kind === 'forbidden') setForbidden(true)
  }, [])

  const onTap = useCallback(
    (fraction: number) => {
      const a = tapAction(fraction, rtl)
      if (a === 'chrome') setChrome((v) => !v)
      else if (paged) go((current ?? 1) + (a === 'next' ? 1 : -1))
      else window.scrollBy({ top: (a === 'next' ? 0.85 : -0.85) * window.innerHeight })
    },
    [rtl, paged, go, current],
  )
  const zoom = useZoom(stageRef, onTap, `${prefs.mode}:${paged ? current : ''}:${prefs.fit}`)

  const act = (a: ComicAction) => {
    if (current === null) return
    if (a === 'next') go(current + 1)
    else if (a === 'prev') go(current - 1)
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

  return { paged, rtl, chrome, forbidden, figures, figureRef, onProblem, zoom, act }
}

export function readerClasses(phone: boolean, chrome: boolean, mode: ComicPrefs['mode']): string {
  return ['comic', 'reader', phone ? 'comic-phone reader-phone' : '', chrome ? '' : 'comic-chrome-off', `comic-mode-${mode}`].filter(Boolean).join(' ')
}
