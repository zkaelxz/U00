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
import { ButtonLink } from '../components/Button'
import { ErrorBanner } from '../components/ErrorBanner'
import { useMediaQuery } from '../hooks/useMediaQuery'
import type { ComicPageInfo } from '../types/comic'
import type { SavedChapterPages } from '../types/savedComics'
import { ComicGoTo, ComicPager, ComicViewControl } from './comic/ComicControls'
import { ComicPageView, FORBIDDEN_TEXT } from './comic/ComicPageView'
import { clampPage, inRenderWindow, ZOOM, type ComicPrefs } from './comic/comicLogic'
import { useJumpRequest, usePageJump } from './comic/usePageJump'
import { readerClasses, usePagedReader } from './comic/usePagedReader'
import {
  hashIsReading, loadLastRead, loadMangaPrefs, mangaReadHref, mangaSeriesHref, pageInfos, saveLastRead, saveMangaPrefs, seriesTitle,
} from './manga/mangaLogic'
import './reader/reader.css'
import './comic/comic.css'
import './manga/manga.css'
import { browserStorage } from '../hooks/usePersistedState'

type Props = { source: string; series: string; chapter: string; page: number | null }

export default function SavedMangaReader({ source, series, chapter, page: routePage }: Props) {
  const phone = useMediaQuery('(max-width: 640px)')
  const [data, setData] = useState<SavedChapterPages | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [stored, setStored] = useState<ComicPrefs | null>(null)
  const [picked, setPicked] = useState<number | null>(null)
  const [seenRoute, setSeenRoute] = useState(routePage)
  const [initialPage] = useState(() => {
    if (routePage !== null) return routePage
    const last = loadLastRead(browserStorage(), source, series)
    return last && last.chapter === chapter ? last.page : 1
  })

  const stageRef = useRef<HTMLDivElement>(null)
  // Continue only follows a reader the user moved; opening a chapter to peek
  // at it, or placeholder layout shifting before images load, must not
  // overwrite where they really were.
  const [moved, setMoved] = useState(false)
  const movedRef = useRef(false)
  const markMoved = useCallback(() => {
    movedRef.current = true
    setMoved(true)
  }, [])

  useEffect(() => {
    getSavedPages(source, series, chapter).then(setData, setError)
  }, [source, series, chapter])

  const pages = useMemo(() => pageInfos(data?.pages ?? []), [data])
  const count = pages.length
  const prefs = useMemo(() => stored ?? loadMangaPrefs(browserStorage(), source, series), [stored, source, series])
  const start = count ? clampPage(initialPage, count) : null
  const current = picked ?? start
  const { activeJump, requestJump } = useJumpRequest(start)
  const hrefAt = useCallback((n: number | null) => mangaReadHref(source, series, chapter, n), [source, series, chapter])

  // The hash changed from outside (typed, Back/Forward): follow it.
  if (routePage !== seenRoute) {
    setSeenRoute(routePage)
    if (routePage !== null && count && current !== null && clampPage(routePage, count) !== current) {
      const n = clampPage(routePage, count)
      setPicked(n)
      requestJump(n)
      markMoved()
    }
  }

  // Put the start page in the hash. In-reader moves replace the history
  // entry, so Back leaves the chapter instead of stepping through its pages.
  useEffect(() => {
    if (start !== null && picked === null && routePage !== start && hashIsReading(window.location.hash, source, series, chapter)) {
      window.location.replace(hrefAt(start))
    }
  }, [start, picked, routePage, hrefAt, source, series, chapter])

  const setPrefs = (next: ComicPrefs) => {
    if (next.mode !== prefs.mode && current !== null) requestJump(current)
    setStored(next)
    saveMangaPrefs(browserStorage(), source, series, next)
  }

  const go = useCallback(
    (n: number, fromScroll = false) => {
      if (!count) return
      const target = clampPage(n, count)
      if (!fromScroll) {
        requestJump(target)
        markMoved()
      }
      setPicked(target)
      if (hashIsReading(window.location.hash, source, series, chapter)) window.location.replace(hrefAt(target))
    },
    [count, hrefAt, source, series, chapter, markMoved, requestJump],
  )

  const srcFor = useCallback((p: ComicPageInfo) => savedPageUrl(source, series, chapter, p.id), [source, series, chapter])
  const { paged, rtl, chrome, forbidden, figureRef, figures, onProblem, zoom, act } = usePagedReader({ stageRef, pages, prefs, current, count, go, srcFor })

  usePageJump({
    activeJump,
    current,
    vertical: prefs.mode === 'vertical',
    pages,
    figures,
    stageRef,
    onVisible: (n) => go(n, true),
    canObserve: () => movedRef.current,
  })


  // Wheel, touch, a scrollbar drag or a scrolling key: the user, not the page, moved.
  useEffect(() => {
    if (moved) return
    const onKey = (e: KeyboardEvent) => {
      if (['ArrowDown', 'ArrowUp', 'PageDown', 'PageUp', 'Home', 'End', ' '].includes(e.key)) markMoved()
    }
    const onPointer = (e: PointerEvent) => {
      if (e.target === document.documentElement) markMoved()
    }
    window.addEventListener('wheel', markMoved, { passive: true })
    window.addEventListener('touchmove', markMoved, { passive: true })
    window.addEventListener('keydown', onKey)
    window.addEventListener('pointerdown', onPointer)
    return () => {
      window.removeEventListener('wheel', markMoved)
      window.removeEventListener('touchmove', markMoved)
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('pointerdown', onPointer)
    }
  }, [moved, markMoved])

  // Where the reader is, for Continue (this browser only).
  useEffect(() => {
    if (moved && current !== null) saveLastRead(browserStorage(), source, series, { chapter, page: current })
  }, [moved, current, source, series, chapter])

  const seriesHref = mangaSeriesHref(source, series)
  const title = data?.title ?? chapter
  const currentPage = current !== null ? pages[current - 1] : undefined
  const z = zoom.zoom
  const zoomed = z.scale > 1
  const label = current !== null && count ? `${current} / ${count}` : ''

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

  return (
    <div className={readerClasses(phone, chrome, prefs.mode)}>
      {phone ? (
        <header className="reader-phone-head comic-head">
          <a href={seriesHref} className="reader-back" aria-label={`Back to ${seriesTitle(series)}`}>‹</a>
          <h1 className="reader-title">{title}</h1>
          {label && <span className="comic-count" data-testid="comic-page-label">{label}</span>}
          {view}
        </header>
      ) : (
        <div className="comic-top comic-head">
          <h1 className="visually-hidden">{title}</h1>
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
                  mounted={paged || inRenderWindow(n, current)}
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
