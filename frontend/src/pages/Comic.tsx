/*
 * Comic viewer (#/comic/<id>[?page=N]) for manga, manhua and manhwa pages.
 * Vertical scroll (manhua/manhwa default) or one page at a time (manga
 * default, right to left). Fit, pinch/double-tap zoom, lazy images with the
 * next pages preloaded, keys and tap thirds, a page scrubber, the typeset
 * image and the text boxes (a side panel on desktop, a bottom Sheet on
 * phones). Without ?page it resumes at the saved page; progress is saved a
 * second after the page changes. Server text is rendered as text only.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { api } from '../api/client'
import { comicApi, comicImageUrl, type ImageProblem } from '../api/comic'
import { ErrorBanner } from '../components/ErrorBanner'
import { Sheet } from '../components/Sheet'
import { useMediaQuery } from '../hooks/useMediaQuery'
import { useShortcut } from '../hooks/useShortcut'
import type { ComicPageInfo, ComicPagesResponse } from '../types/comic'
import { ComicGoTo, ComicPager, ComicViewControl } from './comic/ComicControls'
import { ComicLines } from './comic/ComicText'
import { ComicPageView, FORBIDDEN_TEXT } from './comic/ComicPageView'
import {
  clampPage,
  comicHref,
  defaultPrefs,
  keyAction,
  loadComicPrefs,
  preloadWindow,
  saveComicPrefs,
  tapAction,
  textPages,
  ZOOM,
  type ComicAction,
  type ComicPrefs,
  type RegionsState,
} from './comic/comicLogic'
import { useZoom } from './comic/useZoom'
import './reader/reader.css'
import './comic/comic.css'

const PROGRESS_DELAY_MS = 1000

function browserStorage() {
  try {
    return window.localStorage
  } catch {
    return null
  }
}

// In-viewer moves replace the history entry, so Back leaves the comic
// instead of stepping back through every page.
function replacePage(id: number, page: number) {
  window.location.replace(comicHref(id, page))
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
    // Only the current window is held; older ones can be collected.
    for (const u of [...held.current.keys()]) if (!want.includes(u)) held.current.delete(u)
  }, [key])
}

export default function ComicPage({ id, page: routePage }: { id: number; page: number | null }) {
  const phone = useMediaQuery('(max-width: 640px)')
  const [title, setTitle] = useState<string | null>(null)
  const [data, setData] = useState<ComicPagesResponse | null>(null)
  const [error, setError] = useState<unknown>(null)
  // Saved page from the server (1 when unknown or not allowed).
  const [savedPage, setSavedPage] = useState<number | null>(null)
  // The saved page really came from the server. Until it has (or the reader
  // moves), nothing is saved, so a failed read never overwrites the resume point.
  const [progressKnown, setProgressKnown] = useState(false)
  // A page the viewer moved to; until then the start page (?page or saved).
  const [picked, setPicked] = useState<number | null>(null)
  const [resumeDismissed, setResumeDismissed] = useState(false)
  // A page to bring into view once rendered; seq makes a repeat request new.
  const [jump, setJump] = useState<{ page: number; seq: number } | null>(null)
  const [seenRoute, setSeenRoute] = useState(routePage)
  const [stored, setStored] = useState<ComicPrefs | null>(null)
  const [chrome, setChrome] = useState(true)
  const [forbidden, setForbidden] = useState(false)
  const [regions, setRegions] = useState<Record<number, RegionsState>>({})
  const [linesOpen, setLinesOpen] = useState(false)
  const [initialPage] = useState(routePage)

  const stageRef = useRef<HTMLDivElement>(null)
  const figures = useRef(new Map<number, HTMLElement>())
  // The last jump request brought into view.
  const jumpHandled = useRef(-1)
  const jumpRef = useRef(jump)
  // The page the server already has, so an unchanged page is not re-sent.
  const lastSaved = useRef<number | null>(null)
  // The reader has been on a page other than the start page.
  const moved = useRef(false)
  // Text-box requests already sent, by page id.
  const requested = useRef(new Set<number>())

  useEffect(() => {
    api.getDrama(id).then((d) => setTitle(d.title_en || d.title_zh || `Drama #${d.id}`), () => setTitle(null))
    comicApi.pages(id).then(setData, setError)
    comicApi.progress(id).then(
      (p) => {
        lastSaved.current = p.last_page
        setSavedPage(p.last_page)
        setProgressKnown(true)
      },
      // Unknown (refused or failed): start at page 1 but don't save it.
      () => setSavedPage(1),
    )
  }, [id])

  const count = data?.page_count ?? data?.pages.length ?? 0
  const pages = useMemo(() => data?.pages ?? [], [data])
  const defaults = useMemo(() => defaultPrefs(data?.media_type, data?.reading_mode_default), [data])
  const prefs = useMemo(() => stored ?? loadComicPrefs(browserStorage(), id, defaults), [stored, id, defaults])
  const paged = prefs.mode === 'paged'
  const rtl = paged && prefs.rtl
  const canTypeset = pages.some((p) => p.has_rendered)

  // First page: ?page (clamped), else the saved page once it is known.
  const start =
    !data || count === 0
      ? null
      : initialPage !== null
        ? clampPage(initialPage, count)
        : savedPage !== null
          ? clampPage(savedPage, count)
          : null
  const current = picked ?? start
  const resumed = initialPage === null && !resumeDismissed && start !== null && start > 1 ? start : null
  const activeJump = useMemo(() => jump ?? (start !== null ? { page: start, seq: 0 } : null), [jump, start])
  const requestJump = (page: number) => setJump((j) => ({ page, seq: (j?.seq ?? 0) + 1 }))

  // The hash changed from outside (typed, Back/Forward): follow it. The
  // viewer's own replace() lands here too, already equal to the current page.
  if (routePage !== seenRoute) {
    setSeenRoute(routePage)
    if (routePage !== null && count && current !== null && clampPage(routePage, count) !== current) {
      const n = clampPage(routePage, count)
      setPicked(n)
      requestJump(n)
    }
  }

  // Put the start page in the hash (resume, or a clamped ?page).
  useEffect(() => {
    if (start !== null && picked === null && routePage !== start) replacePage(id, start)
  }, [start, picked, routePage, id])

  const setPrefs = (next: ComicPrefs) => {
    // Switching modes keeps the page.
    if (next.mode !== prefs.mode && current !== null) requestJump(current)
    setStored(next)
    saveComicPrefs(browserStorage(), id, next)
  }

  const go = useCallback(
    (n: number, fromScroll = false) => {
      if (!count) return
      const target = clampPage(n, count)
      if (!fromScroll) {
        setResumeDismissed(true)
        setJump((j) => ({ page: target, seq: (j?.seq ?? 0) + 1 }))
      }
      setPicked(target)
      replacePage(id, target)
    },
    [count, id],
  )

  // Bring a requested page into view once it is rendered: its own figure when
  // scrolling, the top of the stage when a new page is turned.
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

  // Save progress a second after the page settles; a refusal (no lines.edit) is ignored.
  // Without the server's saved page, only a page the reader moved to is saved.
  useEffect(() => {
    if (current === null || !count) return
    if (current !== start) moved.current = true
    if (!progressKnown && !moved.current) return
    const t = setTimeout(() => {
      if (lastSaved.current === current) return
      comicApi.saveProgress(id, current).then(
        (p) => {
          lastSaved.current = p.last_page
        },
        () => {
          // Reading still works without saving.
        },
      )
    }, PROGRESS_DELAY_MS)
    return () => clearTimeout(t)
  }, [id, current, count, start, progressKnown])

  const srcFor = useCallback(
    (p: ComicPageInfo) => comicImageUrl(id, p.id, prefs.typeset && p.has_rendered ? 'rendered' : 'original', p.image_version),
    [id, prefs.typeset],
  )

  const preload = useMemo(
    () => (current === null ? [] : preloadWindow(current, count, prefs.mode).map((n) => pages[n - 1]).filter(Boolean).map(srcFor)),
    [current, count, prefs.mode, pages, srcFor],
  )
  usePreload(forbidden ? [] : preload)

  // Text boxes for the page(s) in view, fetched once each.
  const wantText = prefs.text || linesOpen
  const textKey = current === null || !wantText ? '' : textPages(current, count, prefs.mode).join(',')
  useEffect(() => {
    if (!textKey) return
    for (const n of textKey.split(',').map(Number)) {
      const p = pages[n - 1]
      if (!p || !p.has_regions || requested.current.has(p.id)) continue
      requested.current.add(p.id)
      comicApi.regions(id, p.id).then(
        (res) => setRegions((r) => ({ ...r, [p.id]: res })),
        (e: unknown) => setRegions((r) => ({ ...r, [p.id]: { error: e } })),
      )
    }
  }, [textKey, pages, id])

  const onProblem = useCallback((kind: ImageProblem) => {
    if (kind === 'forbidden') setForbidden(true)
  }, [])

  const zoomBoxKey = `${prefs.mode}:${paged ? current : ''}:${prefs.fit}`
  const onTap = useCallback(
    (fraction: number) => {
      const a = tapAction(fraction, rtl)
      if (a === 'chrome') setChrome((v) => !v)
      else if (paged) go((current ?? 1) + (a === 'next' ? 1 : -1))
      else window.scrollBy({ top: (a === 'next' ? 0.85 : -0.85) * window.innerHeight })
    },
    [rtl, paged, go, current],
  )
  const zoom = useZoom(stageRef, onTap, zoomBoxKey)

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

  const regionsFor = (p: ComicPageInfo): RegionsState | undefined | 'none' => (!p.has_regions ? 'none' : regions[p.id])
  const overlayFor = (p: ComicPageInfo) => {
    if (!prefs.text) return null
    const r = regions[p.id]
    return r && !('error' in r) ? r : null
  }

  const libraryHref = '#/library'
  const workspaceHref = `#/drama/${id}/source`
  const empty = data !== null && count === 0
  const currentPage = current !== null ? pages[current - 1] : undefined
  const z = zoom.zoom
  const zoomed = z.scale > 1

  const figureRef = (n: number) => (el: HTMLElement | null) => {
    if (el) figures.current.set(n, el)
    else figures.current.delete(n)
  }

  const view = (
    <ComicViewControl prefs={prefs} onChange={setPrefs} canTypeset={canTypeset} phone={phone}>
      {phone && current !== null && count > 1 && <ComicGoTo count={count} onGo={go} />}
    </ComicViewControl>
  )

  const toggles = !phone && (
    <div className="comic-toggles" role="group" aria-label="Show">
      <button
        type="button"
        aria-pressed={prefs.typeset && canTypeset}
        disabled={!canTypeset}
        title={canTypeset ? 'Show the typeset (translated) pages' : 'No page has been typeset yet'}
        onClick={() => setPrefs({ ...prefs, typeset: !prefs.typeset })}
      >
        Typeset
      </button>
      <button type="button" aria-pressed={prefs.text} onClick={() => setPrefs({ ...prefs, text: !prefs.text })}>
        Text
      </button>
      <span className="comic-zoom" role="group" aria-label="Zoom">
        <button type="button" aria-label="Zoom out" disabled={!zoomed} onClick={() => act('zoomOut')}>−</button>
        <button type="button" className="comic-zoom-level" aria-label="Reset zoom" disabled={!zoomed} onClick={() => act('zoomReset')}>
          {Math.round(z.scale * 100)}%
        </button>
        <button type="button" aria-label="Zoom in" disabled={z.scale >= ZOOM.max} onClick={() => act('zoomIn')}>+</button>
      </span>
    </div>
  )

  const shownPages: [ComicPageInfo, number][] =
    paged ? (currentPage && current !== null ? [[currentPage, current]] : []) : pages.map((p, i) => [p, i + 1])
  const stage = current !== null && pages.length > 0 && (
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
        {shownPages.map(([p, n]) => {
          return (
            <ComicPageView
              key={`${p.id}:${paged ? 'p' : 'v'}`}
              page={p}
              number={n}
              count={count}
              src={srcFor(p)}
              fit={prefs.fit}
              eager={paged || Math.abs(n - (current ?? 1)) <= 1}
              regions={overlayFor(p)}
              boxText={!phone}
              onProblem={onProblem}
              figureRef={figureRef(n)}
            />
          )
        })}
      </div>
    </div>
  )

  const classes = ['comic', 'reader', phone ? 'comic-phone reader-phone' : '', chrome ? '' : 'comic-chrome-off', `comic-mode-${prefs.mode}`]
  const label = current !== null && count ? `${current} / ${count}` : ''

  return (
    <div className={classes.filter(Boolean).join(' ')}>
      {phone ? (
        <header className="reader-phone-head comic-head">
          <a href={libraryHref} className="reader-back" aria-label="Back to Library">‹</a>
          <span className="reader-title">{title ?? 'Loading…'}</span>
          {label && <span className="comic-count" data-testid="comic-page-label">{label}</span>}
          {view}
        </header>
      ) : (
        <div className="comic-top comic-head">
          <nav aria-label="Breadcrumb" className="reader-crumbs comic-crumbs">
            <a href={libraryHref}>Library</a>
            <span aria-hidden="true"> / </span>
            <a href={workspaceHref}>{title ?? 'Loading…'}</a>
          </nav>
          {current !== null && count > 0 && <ComicPager page={current} count={count} rtl={rtl} onGo={go} />}
          {toggles}
          {view}
        </div>
      )}

      {resumed !== null && (
        <div className="banner reader-note" role="status">
          <span>Resumed at page {resumed}.</span>
          <button type="button" className="link" onClick={() => setResumeDismissed(true)}>Dismiss</button>
        </div>
      )}
      {forbidden && (
        <div className="banner error-banner" role="alert" data-testid="comic-forbidden">
          <strong>{FORBIDDEN_TEXT}</strong>
        </div>
      )}
      <ErrorBanner error={error} />

      {empty ? (
        <section className="panel">
          <p>No pages to read yet.</p>
          <a href={workspaceHref}>Open the workspace</a>
        </section>
      ) : !data || current === null ? (
        !error && <p className="muted">Loading…</p>
      ) : (
        <div className={!phone && prefs.text ? 'comic-body comic-body-panel' : 'comic-body'}>
          {stage}
          {!phone && prefs.text && currentPage && (
            <aside className="comic-panel" aria-label="Page text">
              <h2>Page {current} text</h2>
              <ComicLines page={current} state={regionsFor(currentPage)} />
            </aside>
          )}
        </div>
      )}

      {phone && current !== null && count > 0 && (
        <div className="reader-bottom comic-bottom">
          <ComicPager page={current} count={count} rtl={rtl} onGo={go} compact />
          <button
            type="button"
            className="comic-text-btn"
            aria-pressed={prefs.text}
            onClick={() => {
              if (!prefs.text) setPrefs({ ...prefs, text: true })
              setLinesOpen(true)
            }}
          >
            Text
          </button>
        </div>
      )}
      {phone && currentPage && current !== null && (
        <Sheet open={linesOpen} title={`Page ${current} text`} onClose={() => setLinesOpen(false)}>
          <ComicLines page={current} state={regionsFor(currentPage)} />
          {prefs.text && (
            <button
              type="button"
              onClick={() => {
                setPrefs({ ...prefs, text: false })
                setLinesOpen(false)
              }}
            >
              Hide text boxes
            </button>
          )}
        </Sheet>
      )}
    </div>
  )
}
