/*
 * Reader page (#/read/<id>[?page=N]). The page text is server-built HTML
 * shown in a sandboxed iframe (scripts only, no same-origin), never
 * injected into the React DOM. Without ?page it resumes at the saved page.
 * Folded Sections below: Watch / listen, Words, Vocabulary, Glossary,
 * Story tools and My notes; Sections with no data are not rendered.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react'

import { api, ApiError, keepaliveFetch } from '../api/client'
import { pageHtmlUrl, readerApi, readerEngines, readerEnginesOff } from '../api/reader'
import { patchLine } from '../api/review'
import { translateApi } from '../api/translate'
import { getGlossaryTerms } from '../api/translateStage'
import { ErrorBanner } from '../components/ErrorBanner'
import { RetryBanner } from '../components/RetryBanner'
import { Field } from '../components/Field'
import { Section } from '../components/Section'
import { buttonClass } from '../components/uiClasses'
import { SourceLink } from './discover/ExternalLink'
import { useDelayedSave } from '../hooks/useDelayedSave'
import { useMediaQuery } from '../hooks/useMediaQuery'
import { usePersistedState } from '../hooks/usePersistedState'
import { Breadcrumbs } from '../nav/BreadcrumbNav'
import { routeCrumbs } from '../nav/breadcrumbs'
import { ButtonLink } from '../components/Button'
import { routeHref } from '../router'
import { resolveTheme, useThemePref } from '../theme'
import type {
  ReaderMediaAvailability,
  ReaderOverview,
  ReaderPage as ReaderPageData,
  ReaderVocabList,
} from '../types/reader'
import type { TranslateEngine } from '../types/translate'
import type { GlossaryTerm } from '../types/translateStage'
import { isComicType } from './comic/comicLogic'
import { ActionError, EditToggle } from './reader/ReaderAction'
import { ListenWatch } from './reader/ListenWatch'
import { useLiveFrame } from './reader/useLiveRefresh'
import { ChapterJump } from './reader/ReaderChapters'
import { SaveBookCard } from './reader/SaveBookCard'
import { ReaderTermPanels } from './reader/ReaderTermTools'
import { lineEditPatch } from './reader/readerLineEdit'
import { LINES_CHANGED_MESSAGE } from './workspace/stages/review/reviewLogic'
import { stageLabel } from './workspace/stages'
import { titleKind } from './workspace/titleKind'
import { useSession } from '../hooks/useSession'
import { useChosenForYou } from '../hooks/useChosenForYou'
import { holds } from './diagnostics/adminUsers'
import { useAction } from './reader/useReaderAction'
import { ReaderPrefsControl } from './reader/ReaderPrefs'
import { StorySection } from './reader/ReaderStory'
import { GlossarySection, VocabSection, WordsSection } from './reader/ReaderWords'
import { firstLineOf, fixedStarts, lastLineOf } from './reader/chapterPages'
import {
  clampPage,
  loadPrefs,
  metricsLine,
  pageParams,
  readerEngine,
  resumeTarget,
  savePrefs,
  spoilerBoundary,
  type ReaderPrefs,
} from './reader/readerPrefsStore'
import './reader/reader.css'
import { browserStorage } from '../hooks/usePersistedState'

const PROGRESS_DELAY_MS = 1000

// The Reader then asks the server which page holds the line, since pages follow a novel's chapters.
function goToLine(id: number, line: number) {
  window.location.replace(routeHref({ name: 'read', id, page: null, line }))
}

function goToPage(id: number, page: number, replace = false) {
  const href = routeHref({ name: 'read', id, page })
  if (replace) window.location.replace(href)
  else window.location.hash = href
}

function Pager({ page, count, loading, onGo, goTo, chapters }: {
  chapters?: ReactNode
  page: number
  count: number
  loading: boolean
  onGo: (n: number) => void
  goTo: boolean
}) {
  return (
    <nav className="reader-pager" aria-label="Pages">
      <button type="button" aria-label="Previous page" disabled={page <= 1} onClick={() => onGo(page - 1)}>
        ‹
      </button>
      <span className="reader-page-label" data-testid="reader-page-label" aria-live="polite">
        {loading ? `Loading page ${page}…` : `Page ${page} of ${count}`}
      </span>
      <button type="button" aria-label="Next page" disabled={page >= count} onClick={() => onGo(page + 1)}>
        ›
      </button>
      {chapters}
      {goTo && <GoTo count={count} onGo={onGo} />}
    </nav>
  )
}

function GoTo({ count, onGo, withLabel = false }: { count: number; onGo: (n: number) => void; withLabel?: boolean }) {
  const [value, setValue] = useState('')
  const submit = (e: FormEvent) => {
    e.preventDefault()
    const n = Number(value)
    if (Number.isFinite(n) && n >= 1) onGo(clampPage(n, count))
    setValue('')
  }
  const input = (
    <input
      type="number"
      min={1}
      max={count}
      inputMode="numeric"
      enterKeyHint="go"
      autoComplete="off"
      aria-label={withLabel ? undefined : 'Go to page'}
      placeholder="Page"
      value={value}
      onChange={(e) => setValue(e.target.value)}
    />
  )
  return (
    <form className="reader-goto" onSubmit={submit}>
      {withLabel ? <Field label="Go to page">{input}</Field> : input}
      <button type="submit" disabled={!value}>Go</button>
    </form>
  )
}

function NotesSection({ dramaId }: { dramaId: number }) {
  const [saved, setSaved] = useState<string | null>(null)
  const [text, setText] = useState('')
  const [status, setStatus] = useState<string | null>(null)
  const [loadError, setLoadError] = useState<unknown>(null)
  useEffect(() => {
    readerApi.notes(dramaId).then(
      (n) => {
        setSaved(n.notes)
        setText(n.notes)
      },
      setLoadError,
    )
  }, [dramaId])
  const save = useAction(async () => {
    const n = await readerApi.saveNotes(dramaId, text)
    setSaved(n.notes)
    setStatus('Saved.')
  })
  const dirty = saved !== null && text !== saved
  return (
    <Section title="My notes" storageKey="reader.notes" summary={saved ? 'Has notes' : 'Empty'}>
      <ErrorBanner error={loadError} />
      <Field label="Notes" help="Your own notes for this title. Other accounts don’t see them. Saved when you press Save.">
        <textarea
          rows={5}
          maxLength={100_000}
          value={text}
          disabled={saved === null}
          onChange={(e) => {
            setText(e.target.value)
            setStatus(null)
          }}
        />
      </Field>
      <div className="actions">
        <button type="button" disabled={!dirty || save.busy} onClick={() => void save.run()}>
          {save.busy ? 'Saving…' : 'Save'}
        </button>
        {status && !dirty && <span className="muted" role="status">{status}</span>}
      </div>
      <ActionError action={save} />
    </Section>
  )
}

export default function ReaderPage({ id, page: routePage, line: routeLine = null }: { id: number; page: number | null; line?: number | null }) {
  const phone = useMediaQuery('(max-width: 640px)')
  // The Reader's "auto" theme follows the app theme (the header theme button).
  const appLook = resolveTheme(useThemePref(), useMediaQuery('(prefers-color-scheme: dark)'))
  const [prefs, setPrefsState] = useState<ReaderPrefs>(() => loadPrefs(browserStorage()))
  const [title, setTitle] = useState<string | null>(null)
  const [titleFailed, setTitleFailed] = useState(false)
  const [sourceUrl, setSourceUrl] = useState<string | null>(null)
  const [sourceLanguage, setSourceLanguage] = useState('und')
  const [mediaType, setMediaType] = useState<string | null>(null)
  const [sourceStage, setSourceStage] = useState<string | null>(null) // null until the title loads: the name depends on its kind
  const [overview, setOverview] = useState<ReaderOverview | null>(null)
  const [data, setData] = useState<ReaderPageData | null>(null)
  // The iframe's document URL, set with the counts it belongs to, so a failed
  // page load never swaps the text that is still on screen. The count goes up
  // on every load so the same URL (a lookup or Retry on the same page) still
  // reloads the frame, which a src that did not change would not.
  const [pageSrc, setPageSrc] = useState<{ url: string; load: number } | null>(null)
  // The title or the overview failed to load. Separate from a page failure so a
  // page that loads fine can't clear it.
  const [loadError, setLoadError] = useState<unknown>(null)
  // A failed page fetch belongs to the page it was for, so moving on drops it.
  const [pageFailure, setPageFailure] = useState<{ page: number; error: unknown } | null>(null)
  const [resumed, setResumed] = useState<number | null>(null)
  const [lastIdx, setLastIdx] = useState<{ page: number; size: number; idx: number } | null>(null)
  const [media, setMedia] = useState<ReaderMediaAvailability | null>(null)
  const [vocab, setVocab] = useState<ReaderVocabList | null>(null)
  const [terms, setTerms] = useState<GlossaryTerm[]>([])
  const [engines, setEngines] = useState<TranslateEngine[]>([])
  const [enginesOff, setEnginesOff] = useState<TranslateEngine[]>([])
  const [enginePref, setEngine] = usePersistedState('reader.engine', '')
  const [pageTick, setPageTick] = useState(0)
  const [loadTick, setLoadTick] = useState(0)
  const [initialPage] = useState(routePage)
  const [vocabTick, setVocabTick] = useState(0)

  // Title A's link must not show on title B while B loads, or if B's load fails.
  const [seenLoad, setSeenLoad] = useState({ id, loadTick })
  if (seenLoad.id !== id || seenLoad.loadTick !== loadTick) {
    setSeenLoad({ id, loadTick })
    setSourceUrl(null)
    setTitleFailed(false)
    setLoadError(null)
  }
  useEffect(() => {
    let cancelled = false
    api.getDrama(id).then(
      (d) => {
        if (cancelled) return
        setTitle(d.title_en || d.title_zh || `Title #${d.id}`)
        setSourceUrl(d.source_url ?? null)
        if (d.source_language) setSourceLanguage(d.source_language)
        setMediaType(d.media_type)
        setSourceStage(stageLabel('source', titleKind(d.media_type, d.content_mode)))
      },
      (e) => {
        if (cancelled) return
        setTitleFailed(true)
        setLoadError(e)
      },
    )
    readerApi.overview(id).then(setOverview, setLoadError)
    readerApi.media(id).then(setMedia, () => setMedia(null))
    getGlossaryTerms(id).then(setTerms, () => setTerms([]))
    translateApi.engines().then((all) => {
      setEngines(readerEngines(all))
      setEnginesOff(readerEnginesOff(all))
    }, () => setEngines([]))
    return () => {
      cancelled = true
    }
  }, [id, loadTick])

  useEffect(() => {
    readerApi.vocab(id).then(setVocab, () => setVocab(null))
  }, [id, vocabTick])

  const empty = overview !== null && overview.line_count === 0

  // A manga/manhua/manhwa with no lines is read as pages (History links carry no type).
  useEffect(() => {
    if (empty && isComicType(mediaType)) window.location.replace(routeHref({ name: 'comic', id, page: initialPage }))
  }, [empty, mediaType, id, initialPage])
  // Until a page arrives with the server's own list, pages are counted as fixed blocks.
  const starts = data?.page_starts ?? fixedStarts(overview?.line_count ?? 0, prefs.chapterSize)
  const count = starts.length

  // No ?page: open the page holding ?line, else resume where the reader left off (said when past page 1).
  useEffect(() => {
    if (routePage !== null || !overview || empty) return
    const target = routeLine !== null ? { line: routeLine } : resumeTarget(overview)
    if ('page' in target) {
      if (target.page > 1) setResumed(target.page)
      goToPage(id, target.page, true)
      return
    }
    let live = true
    readerApi.page(id, { ...pageParams(prefs, 1, { phone, appLook }), line: target.line }).then(
      (d) => {
        if (!live) return
        if (routeLine === null && d.page > 1) setResumed(d.page)
        goToPage(id, d.page, true)
      },
      (e: unknown) => live && setLoadError(e),
    )
    return () => {
      live = false
    }
    // Only the line, the title and the page size choose the page; the look of the page does not.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routePage, routeLine, overview, empty, id, prefs.chapterSize])

  const page = routePage
  const [editMode, setEditMode] = useState(false)
  const params = useMemo(
    () => (page === null ? null : { ...pageParams(prefs, page, { phone, appLook }), ...(editMode ? { edit: true } : {}) }),
    [prefs, page, phone, appLook, editMode],
  )
  const frameRef = useRef<HTMLIFrameElement>(null)
  const session = useSession()
  const canEdit = holds(session, 'lines.edit')
  const [editing, setEditing] = useState<{ lineId: number; seen: string; en: string; page: number } | null>(null)
  const [editNote, setEditNote] = useState<string | null>(null)
  const save = useAction(async (lineId: number, seen: string, en: string) => {
    const patch = lineEditPatch(seen, en)
    if (patch) {
      try {
        await patchLine(id, lineId, patch)
      } catch (e) {
        if (e instanceof ApiError && e.status === 409) {
          setEditNote(LINES_CHANGED_MESSAGE)
          setEditing((cur) => (cur?.lineId === lineId ? null : cur))
          setPageTick((n) => n + 1)
          return
        }
        throw e
      }
    }
    // A save for one line never closes the box of another.
    setEditing((cur) => (cur?.lineId === lineId ? null : cur))
    if (patch) setPageTick((n) => n + 1)
  })
  const closeEdit = () => {
    setEditing(null)
    save.clearError()
  }
  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      // The frame is sandboxed to an opaque origin, so its window is the only way to know it sent this.
      if (!editMode || e.source !== frameRef.current?.contentWindow) return
      const m = e.data as { baiheReader?: string; lineId?: unknown; en?: unknown }
      if (m?.baiheReader !== 'edit' || typeof m.lineId !== 'number' || page === null) return
      const lineId = m.lineId
      const seen = typeof m.en === 'string' ? m.en : ''
      // An open draft is never replaced by another line's pencil, and nothing opens while a save is out.
      setEditing((cur) => (cur && (cur.en !== cur.seen || save.busy) ? cur : { lineId, seen, en: seen, page }))
      setEditNote(null)
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [editMode, page, save.busy])

  const pageFirst = page === null ? 0 : firstLineOf(starts, page)
  const pageLast = page === null ? 0 : lastLineOf(starts, page, overview?.line_count ?? 0)
  const live = useLiveFrame(id, page, pageFirst, pageLast, frameRef, editing !== null || save.busy, () => setPageTick((n) => n + 1))

  const overviewReady = overview !== null
  // Read inside the page effect without re-running it when the shown page changes.
  const shownRef = useRef<number | null>(null)
  useEffect(() => {
    shownRef.current = data?.page ?? null
  }, [data])
  useEffect(() => {
    if (!params || !overviewReady || empty) return
    let live = true
    readerApi.page(id, params).then(
      (d) => {
        if (!live) return
        setData(d)
        setPageSrc((s) => ({ url: pageHtmlUrl(id, params), load: (s?.load ?? 0) + 1 }))
        // Reloading the page we fell back to must not wipe the failure we are showing.
        setPageFailure((f) => (f && f.page === d.page ? null : f))
      },
      (e: unknown) => {
        if (!live) return
        const pc = (e instanceof ApiError && (e.details as { page_count?: number } | undefined)?.page_count) || null
        if (pc && params.page > pc) goToPage(id, pc, true)
        else {
          setPageFailure({ page: params.page, error: e })
          // Keep the URL and the indicator on the page still on screen.
          if (shownRef.current !== null && shownRef.current !== params.page) goToPage(id, shownRef.current, true)
        }
      },
    )
    return () => {
      live = false
    }
  }, [id, params, overviewReady, empty, pageTick])

  // Save progress a second after a page shows; it also tells us the page's
  // exact last line for the spoiler boundary.
  const shownPage = data?.page ?? null
  const chapterSize = prefs.chapterSize
  const progressToSave = useMemo(
    () => (shownPage === null ? null : { id, page: shownPage, size: chapterSize }),
    [id, shownPage, chapterSize],
  )
  useDelayedSave(progressToSave, PROGRESS_DELAY_MS, (p, leaving) => {
    // Leaving the page: the reply has nowhere to go, but the save must still reach the server.
    readerApi.saveProgress(p.id, p.page, p.size, leaving ? keepaliveFetch : undefined).then(
      (r) => {
        if (leaving) return
        setLastIdx({ page: p.page, size: p.size, idx: r.last_line_idx })
        setOverview((o) => (o ? { ...o, percent_complete: r.percent_complete, last_page: r.last_page, last_line_idx: r.last_line_idx } : o))
      },
      () => {
        // No lines.edit permission or a transient error: reading still works.
      },
    )
  })

  const setPrefs = useCallback(
    (next: ReaderPrefs) => {
      // The first line in view stays in view.
      if (page !== null && next.chapterSize !== prefs.chapterSize) goToLine(id, pageFirst)
      setPrefsState(next)
      savePrefs(browserStorage(), next)
    },
    [id, page, prefs.chapterSize, pageFirst],
  )

  const chosen = useChosenForYou()
  const engine = chosen.auto ? null : readerEngine(engines, enginePref) // null: the server picks one the account may use

  const known = lastIdx && lastIdx.page === page && lastIdx.size === prefs.chapterSize ? lastIdx.idx : null
  const boundary = page === null || !overview
    ? undefined
    : spoilerBoundary(prefs.spoilerFree, known, pageLast)

  // The label and frame show the move to another page until its HTML arrives.
  const pageError =
    page !== null && pageFailure && (pageFailure.page === page || data?.page === page) ? pageFailure.error : null
  // A failure on a page we could not leave: its text must not sit under another page number.
  const stale = page !== null && data !== null && data.page !== page
  const pageLoading = page !== null && !loadError && !pageError && data?.page !== page

  const retryPage = () => {
    const failed = pageFailure?.page ?? null
    setPageFailure(null)
    // Back on the last good page after a failure: go to the page that failed again.
    if (failed !== null && failed !== page) goToPage(id, failed)
    else setPageTick((n) => n + 1)
  }
  const onGo = (n: number) => {
    setResumed(null)
    setPageFailure(null)
    setEditing(null)
    goToPage(id, clampPage(n, count))
  }
  const workspaceHref = routeHref({ name: 'drama', id, stage: 'source' })
  const metrics = overview && !empty ? metricsLine(overview) : null
  const aa = (
    <ReaderPrefsControl prefs={prefs} onChange={setPrefs} phone={phone}>
      {phone && page !== null && !empty && (
        <>
          {metrics && <p className="muted" data-testid="reader-metrics">{metrics}</p>}
          <GoTo count={count} onGo={onGo} withLabel />
        </>
      )}
    </ReaderPrefsControl>
  )

  const editToggle = !empty && canEdit && (
    <EditToggle
      pressed={editMode}
      onToggle={() => {
        setEditMode((v) => !v)
        setEditing(null)
        setEditNote(null)
      }}
    />
  )

  return (
    <div className={phone ? 'reader reader-phone' : 'reader'}>
      {phone ? (
        <header className="reader-phone-head">
          <a href={routeHref({ name: 'library' })} className="reader-back" aria-label="Back to Library">‹</a>
          <h1 className="reader-title">{title ?? (titleFailed ? 'This title' : 'Loading…')}</h1>
          <SourceLink href={sourceUrl} dramaId={id} />
          {editToggle}
          {aa}
        </header>
      ) : (
        <div className="reader-top">
          <h1 className="visually-hidden">{title ?? (titleFailed ? 'This title' : 'Loading…')}</h1>
          <Breadcrumbs crumbs={routeCrumbs({ name: 'read', id, page: null }, { title })} />
          <SourceLink href={sourceUrl} dramaId={id} className={buttonClass('secondary', 'sm', 'reader-source')} />
          {aa}
        </div>
      )}

      {resumed !== null && (
        <div className="banner reader-note" role="status">
          <span>Resumed at page {resumed}.</span>
          <button type="button" className="link" onClick={() => setResumed(null)}>Dismiss</button>
        </div>
      )}
      <RetryBanner error={loadError} what="this title" onRetry={() => setLoadTick((n) => n + 1)} />
      <RetryBanner error={pageError} what={`page ${pageFailure?.page}`} onRetry={retryPage} />

      {empty ? (
        <section className="panel">
          <p>No lines to read yet.</p>
          <ButtonLink href={workspaceHref}>{sourceStage ? `Add lines on ${sourceStage}` : 'Add lines'}</ButtonLink>
        </section>
      ) : (
        <>
          <ListenWatch dramaId={id} media={media} setMedia={setMedia} sourceLanguage={sourceLanguage} />
          {!phone && page !== null && (
            <div className="reader-bar">
              <Pager page={page} count={count} loading={pageLoading} onGo={onGo} goTo chapters={<ChapterJump dramaId={id} page={page} starts={starts} onGo={onGo} />} />
              {editToggle}
              {metrics && <span className="muted" data-testid="reader-metrics">{metrics}</span>}
            </div>
          )}
          {live.note && <p className="muted reader-translating" role="status">{live.note}…</p>}
          {editNote && <p className="error" role="alert">{editNote}</p>}
          {editing && editing.page === page && (
            <form
              className="reader-edit-form"
              onSubmit={(e: FormEvent) => {
                e.preventDefault()
                void save.run(editing.lineId, editing.seen, editing.en)
              }}
            >
              <textarea
                data-testid="reader-edit-box"
                aria-label="Edit"
                value={editing.en}
                rows={4}
                autoFocus
                onChange={(e) => setEditing({ ...editing, en: e.target.value })}
                onKeyDown={(e) => {
                  if (e.key === 'Escape' && !save.busy) closeEdit()
                }}
              />
              <button type="submit" className={buttonClass()} disabled={save.busy}>Save</button>
              <button type="button" className={buttonClass()} disabled={save.busy} onClick={closeEdit}>Cancel</button>
              <ActionError action={save} />
            </form>
          )}
          <ReaderTermPanels dramaId={id} editMode={editMode} frameRef={frameRef} terms={terms}
            onTermsLoaded={setTerms} onPageChanged={() => setPageTick((n) => n + 1)} />
          {data && pageSrc && !(stale && pageError !== null) && (
            <iframe
              ref={frameRef}
              key={pageSrc.load}
              className={pageLoading ? 'reader-frame reader-frame-loading' : 'reader-frame'}
              title={`Page ${data.page} text`}
              sandbox="allow-scripts"
              referrerPolicy="no-referrer"
              src={pageSrc.url}
              aria-busy={pageLoading}
              onLoad={live.onFrameLoad}
            />
          )}
          {!data && !loadError && !pageError && <p className="muted">Loading…</p>}
          {/* After the page text, so the Sections don't jump down when it arrives. */}
          {page !== null && overview && data && (
            <div className="reader-sections">
              <WordsSection
                dramaId={id}
                page={page}
                chapterSize={prefs.chapterSize}
                language={sourceLanguage}
                engines={engines}
                enginesOff={enginesOff}
                engine={engine} chosen={chosen}
                onEngine={setEngine}
                onLookedUp={() => {
                  setPageTick((n) => n + 1)
                  setVocabTick((n) => n + 1)
                }}
              />
              {vocab && vocab.count > 0 && (
                <VocabSection dramaId={id} vocab={vocab} onChanged={() => setVocabTick((n) => n + 1)} />
              )}
              {terms.length > 0 && <GlossarySection dramaId={id} terms={terms} />}
              <StorySection
                dramaId={id}
                page={page}
                chapterSize={prefs.chapterSize}
                boundary={boundary}
                spoilerFree={prefs.spoilerFree}
                engines={engines}
                enginesOff={enginesOff}
                engine={engine} chosen={chosen}
                onEngine={setEngine}
              />
              <NotesSection dramaId={id} />
              <SaveBookCard id={id} canRead={holds(session, 'lines.read')} />
            </div>
          )}
          {phone && page !== null && (
            <div className="reader-bottom">
              <Pager page={page} count={count} loading={pageLoading} onGo={onGo} goTo={false} chapters={<ChapterJump dramaId={id} page={page} starts={starts} onGo={onGo} />} />
            </div>
          )}
        </>
      )}
    </div>
  )
}
