/*
 * Reader for one saved chapter (#/manga/<source>/<series>/<chapter>[?page=N]):
 * the comic viewer's page view, view settings ("Aa": vertical scroll or one
 * page at a time, right to left, fit), pager, zoom, keys and tap thirds, over
 * the page images inside the CBZ file. Without ?page it resumes where this
 * browser left off in this chapter. Where the reader is (chapter and page) is
 * kept per series in localStorage, so the list can offer Continue. Previous
 * and next chapter links sit under the pages.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { getSavedPages, savedPageUrl } from '../api/savedComics'
import type { ImageProblem } from '../api/comic'
import { ButtonLink } from '../components/Button'
import { ErrorBanner } from '../components/ErrorBanner'
import { useMediaQuery } from '../hooks/useMediaQuery'
import { useShortcut } from '../hooks/useShortcut'
import type { ComicPageInfo } from '../types/comic'
import type { SavedChapterPages } from '../types/savedComics'
import { ComicGoTo, ComicPager, ComicViewControl } from './comic/ComicControls'
import { ComicPageView, FORBIDDEN_TEXT } from './comic/ComicPageView'
import { clampPage, keyAction, preloadWindow, tapAction, ZOOM, type ComicAction, type ComicPrefs } from './comic/comicLogic'
import { useZoom } from './comic/useZoom'
import {
  loadLastRead, loadMangaPrefs, mangaReadHref, mangaSeriesHref, pageInfos, saveLastRead, saveMangaPrefs, seriesTitle,
} from './manga/mangaLogic'
import './reader/reader.css'
import './comic/comic.css'
import './manga/manga.css'

function browserStorage() {
  try {
    return window.localStorage
  } catch {
    return null
  }
}

// Keeps the next pages' images warm in the browser cache.
function usePreload(urls: string[]) {
  const held = useRef(new Map<string, HTMLImageElement>())
  const key = urls.join('\n')
  useEffect(() => {
    const want = key ? key.split('\n') : []
    for (const u of want) {
      if (held.current.has(u)) continue
      const img = new Image()
      img.decoding = 'async'
      img.src = u
      held.current.set(u, img)
    }
    for (const u of [...held.current.keys()]) if (!want.includes(u)) held.current.delete(u)
  }, [key])
}

type Props = { source: string; series: string; chapter: string; page: number | null }

export default function SavedMangaReader({ source, series, chapter, page: routePage }: Props) {
  const phone = useMediaQuery('(max-width: 640px)')
  const [data, setData] = useState<SavedChapterPages | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [stored, setStored] = useState<ComicPrefs | null>(null)
  const [picked, setPicked] = useState<number | null>(null)
  const [jump, setJump] = useState<{ page: number; seq: number } | null>(null)
  const [seenRoute, setSeenRoute] = useState(routePage)
  const [chrome, setChrome] = useState(true)
  const [forbidden, setForbidden] = useState(false)
  const [initialPage] = useState(() => {
    if (routePage !== null) return routePage
    const last = loadLastRead(browserStorage(), source, series)
    return last && last.chapter === chapter ? last.page : 1
  })

  const stageRef = useRef<HTMLDivElement>(null)
  const figures = useRef(new Map<number, HTMLElement>())
  const jumpHandled = useRef(-1)
  const jumpRef = useRef(jump)

  useEffect(() => {
    getSavedPages(source, series, chapter).then(setData, setError)
  }, [source, series, chapter])

  const pages = useMemo(() => pageInfos(data?.pages ?? []), [data])
  const count = pages.length
  const prefs = useMemo(() => stored ?? loadMangaPrefs(browserStorage(), source, series), [stored, source, series])
  const paged = prefs.mode === 'paged'
  const rtl = paged && prefs.rtl
  const start = count ? clampPage(initialPage, count) : null
  const current = picked ?? start
  const activeJump = useMemo(() => jump ?? (start !== null ? { page: start, seq: 0 } : null), [jump, start])
  const requestJump = (n: number) => setJump((j) => ({ page: n, seq: (j?.seq ?? 0) + 1 }))
  const hrefAt = useCallback((n: number | null) => mangaReadHref(source, series, chapter, n), [source, series, chapter])

  // The hash changed from outside (typed, Back/Forward): follow it.
  if (routePage !== seenRoute) {
    setSeenRoute(routePage)
    if (routePage !== null && count && current !== null && clampPage(routePage, count) !== current) {
      const n = clampPage(routePage, count)
      setPicked(n)
      requestJump(n)
    }
  }

  // Put the start page in the hash. In-reader moves replace the history
  // entry, so Back leaves the chapter instead of stepping through its pages.
  useEffect(() => {
    if (start !== null && picked === null && routePage !== start) window.location.replace(hrefAt(start))
  }, [start, picked, routePage, hrefAt])

  const setPrefs = (next: ComicPrefs) => {
    if (next.mode !== prefs.mode && current !== null) requestJump(current)
    setStored(next)
    saveMangaPrefs(browserStorage(), source, series, next)
  }

  const go = useCallback(
    (n: number, fromScroll = false) => {
      if (!count) return
      const target = clampPage(n, count)
      if (!fromScroll) setJump((j) => ({ page: target, seq: (j?.seq ?? 0) + 1 }))
      setPicked(target)
      window.location.replace(hrefAt(target))
    },
    [count, hrefAt],
  )

  useEffect(() => {
    jumpRef.current = activeJump
  })
  useEffect(() => {
    if (!activeJump || jumpHandled.current === activeJump.seq || current !== activeJump.page) return
    const el = prefs.mode === 'vertical' ? figures.current.get(activeJump.page) : stageRef.current
    if (!el) return
    jumpHandled.current = activeJump.seq
    const top = el.getBoundingClientRect().top
    if (top < 0 || top > window.innerHeight / 2) el.scrollIntoView({ block: 'start' })
  }, [activeJump, current, prefs.mode, pages])

  // Vertical: the page crossing the middle of the screen is the current one.
  const ready = current !== null
  useEffect(() => {
    if (prefs.mode !== 'vertical' || pages.length === 0 || !ready) return
    if (typeof IntersectionObserver === 'undefined') return
    const io = new IntersectionObserver(
      (entries) => {
        const j = jumpRef.current
        if (j && jumpHandled.current !== j.seq) return
        for (const e of entries) {
          if (!e.isIntersecting) continue
          const n = Number((e.target as HTMLElement).dataset.page)
          if (n) go(n, true)
        }
      },
      { rootMargin: '-50% 0px -50% 0px', threshold: 0 },
    )
    for (const el of figures.current.values()) io.observe(el)
    return () => io.disconnect()
  }, [prefs.mode, pages, go, ready])

  // Where the reader is, for Continue (this browser only).
  useEffect(() => {
    if (current !== null) saveLastRead(browserStorage(), source, series, { chapter, page: current })
  }, [current, source, series, chapter])

  const srcFor = useCallback((p: ComicPageInfo) => savedPageUrl(source, series, chapter, p.id), [source, series, chapter])
  const preload = useMemo(
    () => (current === null ? [] : preloadWindow(current, count, prefs.mode).map((n) => pages[n - 1]).filter(Boolean).map(srcFor)),
    [current, count, prefs.mode, pages, srcFor],
  )
  usePreload(forbidden ? [] : preload)

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

  const seriesHref = mangaSeriesHref(source, series)
  const title = data?.title ?? chapter
  const currentPage = current !== null ? pages[current - 1] : undefined
  const z = zoom.zoom
  const zoomed = z.scale > 1
  const label = current !== null && count ? `${current} / ${count}` : ''

  const figureRef = (n: number) => (el: HTMLElement | null) => {
    if (el) figures.current.set(n, el)
    else figures.current.delete(n)
  }

  const view = (
    <ComicViewControl prefs={prefs} onChange={setPrefs} canTypeset={false} phone={phone}>
      {phone && current !== null && count > 1 && <ComicGoTo count={count} onGo={go} />}
    </ComicViewControl>
  )

  const chapterNav = data && (data.prev_chapter || data.next_chapter) && (
    <nav className="manga-chapter-nav" aria-label="Chapters">
      {data.prev_chapter && <ButtonLink href={mangaReadHref(source, series, data.prev_chapter, 1)}>Previous chapter</ButtonLink>}
      {data.next_chapter && <ButtonLink variant="primary" href={mangaReadHref(source, series, data.next_chapter, 1)}>Next chapter</ButtonLink>}
    </nav>
  )

  const shownPages: [ComicPageInfo, number][] =
    paged ? (currentPage && current !== null ? [[currentPage, current]] : []) : pages.map((p, i) => [p, i + 1])

  const classes = ['comic', 'reader', phone ? 'comic-phone reader-phone' : '', chrome ? '' : 'comic-chrome-off', `comic-mode-${prefs.mode}`]

  return (
    <div className={classes.filter(Boolean).join(' ')}>
      {phone ? (
        <header className="reader-phone-head comic-head">
          <a href={seriesHref} className="reader-back" aria-label={`Back to ${seriesTitle(series)}`}>‹</a>
          <span className="reader-title">{title}</span>
          {label && <span className="comic-count" data-testid="comic-page-label">{label}</span>}
          {view}
        </header>
      ) : (
        <div className="comic-top comic-head">
          <nav aria-label="Breadcrumb" className="reader-crumbs comic-crumbs">
            <a href="#/manga">Saved manga</a>
            <span aria-hidden="true"> / </span>
            <a href={seriesHref}>{seriesTitle(series)}</a>
            <span aria-hidden="true"> / </span>
            <span>{title}</span>
          </nav>
          {current !== null && count > 0 ? (
            <ComicPager page={current} count={count} rtl={rtl} onGo={go} />
          ) : (
            data === null && !error && <ComicPager page={1} count={1} rtl={rtl} onGo={go} loading />
          )}
          <span className="comic-zoom" role="group" aria-label="Zoom">
            <button type="button" aria-label="Zoom out" disabled={!zoomed} onClick={() => act('zoomOut')}>−</button>
            <button type="button" className="comic-zoom-level" aria-label="Reset zoom" disabled={!zoomed} onClick={() => act('zoomReset')}>
              {Math.round(z.scale * 100)}%
            </button>
            <button type="button" aria-label="Zoom in" disabled={z.scale >= ZOOM.max} onClick={() => act('zoomIn')}>+</button>
          </span>
          {view}
        </div>
      )}

      {forbidden && (
        <div className="banner error-banner" role="alert" data-testid="comic-forbidden">
          <strong>{FORBIDDEN_TEXT}</strong>
        </div>
      )}
      <ErrorBanner error={error} />

      {data !== null && count === 0 ? (
        <p className="muted">This chapter has no page images.</p>
      ) : current === null ? (
        !error && <p className="muted">Loading…</p>
      ) : (
        <div className="comic-body">
          <div
            ref={stageRef}
            className={`comic-stage comic-stage-${prefs.mode}${zoomed ? ' comic-zoomed' : ''}`}
            data-testid="comic-stage"
            {...zoom.handlers}
          >
            <div
              className="comic-zoom-layer"
              style={zoomed ? { transform: `translate(${z.x}px, ${z.y}px) scale(${z.scale})` } : undefined}
              data-testid="comic-zoom-layer"
            >
              {shownPages.map(([p, n]) => (
                <ComicPageView
                  key={`${p.id}:${paged ? 'p' : 'v'}`}
                  page={p}
                  number={n}
                  count={count}
                  src={srcFor(p)}
                  fit={prefs.fit}
                  eager={paged || Math.abs(n - current) <= 1}
                  regions={null}
                  boxText={false}
                  onProblem={onProblem}
                  figureRef={figureRef(n)}
                />
              ))}
            </div>
          </div>
        </div>
      )}
      {(!paged || current === count) && chapterNav}

      {phone && current !== null && count > 0 && (
        <div className="reader-bottom comic-bottom">
          <ComicPager page={current} count={count} rtl={rtl} onGo={go} compact />
        </div>
      )}
    </div>
  )
}
