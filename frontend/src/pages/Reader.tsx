/*
 * Reader page (#/read/<id>[?page=N]). The page text is server-built HTML
 * shown in a sandboxed iframe (scripts only, no same-origin), never
 * injected into the React DOM. Without ?page it resumes at the saved page.
 * Folded Sections below: Watch / listen, Words, Vocabulary, Glossary,
 * Story tools and My notes; Sections with no data are not rendered.
 */
import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'

import { api, ApiError } from '../api/client'
import { mediaStreamUrl } from '../api/media'
import { captionUrl, dubTrackUrl, readerApi, readerEngines } from '../api/reader'
import { translateApi } from '../api/translate'
import { getGlossaryTerms } from '../api/translateStage'
import { ErrorBanner } from '../components/ErrorBanner'
import { Field } from '../components/Field'
import { Section } from '../components/Section'
import { useMediaQuery } from '../hooks/useMediaQuery'
import { usePersistedState } from '../hooks/usePersistedState'
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
import { ActionError } from './reader/ReaderAction'
import { useAction } from './reader/useReaderAction'
import { ReaderPrefsControl } from './reader/ReaderPrefs'
import { StorySection } from './reader/ReaderStory'
import { GlossarySection, VocabSection, WordsSection } from './reader/ReaderWords'
import {
  clampPage,
  keepPlace,
  loadPrefs,
  metricsLine,
  pageCount,
  pageParams,
  resumePage,
  savePrefs,
  spoilerBoundary,
  type ReaderPrefs,
} from './reader/readerPrefsStore'
import './reader/reader.css'

const PROGRESS_DELAY_MS = 1000

function browserStorage() {
  try {
    return window.localStorage
  } catch {
    return null
  }
}

function goToPage(id: number, page: number, replace = false) {
  const href = routeHref({ name: 'read', id, page })
  if (replace) window.location.replace(href)
  else window.location.hash = href
}

function Pager({ page, count, loading, onGo, goTo }: {
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

function WatchSection({ dramaId, media, sourceLanguage }: { dramaId: number; media: ReaderMediaAvailability; sourceLanguage: string }) {
  const [failed, setFailed] = useState(false)
  const kinds = [media.original === 'video' ? 'video' : media.original === 'audio' ? 'audio' : null, media.dub ? 'dub' : null, media.narration ? 'narration' : null]
  const summary = kinds.filter(Boolean).join(' · ')
  const lang = (t: string) => (t === 'English' ? 'en' : t === 'Source' ? sourceLanguage : 'und')
  return (
    <Section title="Watch / listen" storageKey="reader.media" summary={summary}>
      {media.original === 'video' && (
        <video className="reader-video" controls preload="metadata" src={mediaStreamUrl(dramaId, 'video')} onError={() => setFailed(true)}>
          {media.caption_tracks.map((t, i) => (
            <track key={t} kind="subtitles" label={t} srcLang={lang(t)} src={captionUrl(dramaId, t)} default={i === 0} />
          ))}
        </video>
      )}
      {media.original === 'audio' && (
        <audio controls preload="metadata" src={mediaStreamUrl(dramaId, 'audio')} onError={() => setFailed(true)} />
      )}
      {(media.dub || media.narration) && (
        <div className="stack">
          <span className="muted">{media.narration && !media.dub ? 'Narration' : 'Dub'}</span>
          <audio controls preload="none" src={dubTrackUrl(dramaId)} onError={() => setFailed(true)} />
        </div>
      )}
      {failed && <p className="muted">Can't play this here. It needs media playback permission, or the file is missing.</p>}
    </Section>
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
      <Field label="Notes" help="Notes for this drama. Anyone with access to this library can see them. Saved when you press Save.">
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

export default function ReaderPage({ id, page: routePage }: { id: number; page: number | null }) {
  const phone = useMediaQuery('(max-width: 640px)')
  // The Reader's "auto" theme follows the app theme (the header theme button).
  const appLook = resolveTheme(useThemePref(), useMediaQuery('(prefers-color-scheme: dark)'))
  const [prefs, setPrefsState] = useState<ReaderPrefs>(() => loadPrefs(browserStorage()))
  const [title, setTitle] = useState<string | null>(null)
  const [sourceLanguage, setSourceLanguage] = useState('und')
  const [mediaType, setMediaType] = useState<string | null>(null)
  const [overview, setOverview] = useState<ReaderOverview | null>(null)
  const [data, setData] = useState<ReaderPageData | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [resumed, setResumed] = useState<number | null>(null)
  const [lastIdx, setLastIdx] = useState<{ page: number; size: number; idx: number } | null>(null)
  const [media, setMedia] = useState<ReaderMediaAvailability | null>(null)
  const [vocab, setVocab] = useState<ReaderVocabList | null>(null)
  const [terms, setTerms] = useState<GlossaryTerm[]>([])
  const [engines, setEngines] = useState<TranslateEngine[]>([])
  const [enginePref, setEngine] = usePersistedState('reader.engine', '')
  const [pageTick, setPageTick] = useState(0)
  const [initialPage] = useState(routePage)
  const [initialSize] = useState(prefs.chapterSize)
  const [vocabTick, setVocabTick] = useState(0)

  // One-off loads for this drama.
  useEffect(() => {
    api.getDrama(id).then(
      (d) => {
        setTitle(d.title_en || d.title_zh || `Drama #${d.id}`)
        if (d.source_language) setSourceLanguage(d.source_language)
        setMediaType(d.media_type)
      },
      setError,
    )
    readerApi.overview(id).then((ov) => {
      setOverview(ov)
      // Opened without ?page: say where we resumed (the effect below navigates).
      if (initialPage === null && ov.line_count > 0) {
        const target = resumePage(ov, initialSize)
        if (target > 1) setResumed(target)
      }
    }, setError)
    readerApi.media(id).then(setMedia, () => setMedia(null))
    getGlossaryTerms(id).then(setTerms, () => setTerms([]))
    translateApi.engines().then((all) => setEngines(readerEngines(all)), () => setEngines([]))
  }, [id, initialPage, initialSize])

  useEffect(() => {
    readerApi.vocab(id).then(setVocab, () => setVocab(null))
  }, [id, vocabTick])

  const empty = overview !== null && overview.line_count === 0

  // A manga/manhua/manhwa with no lines is read as pages (History links carry no type).
  useEffect(() => {
    if (empty && isComicType(mediaType)) window.location.replace(routeHref({ name: 'comic', id, page: initialPage }))
  }, [empty, mediaType, id, initialPage])
  const count = overview ? pageCount(overview.line_count, prefs.chapterSize) : data?.page_count ?? 1

  // No ?page: resume where the reader left off.
  useEffect(() => {
    if (routePage !== null || !overview || empty) return
    goToPage(id, resumePage(overview, prefs.chapterSize), true)
  }, [routePage, overview, empty, id, prefs.chapterSize])

  const page = routePage
  const params = useMemo(
    () => (page === null ? null : pageParams(prefs, page, { phone, appLook })),
    [prefs, page, phone, appLook],
  )

  const overviewReady = overview !== null
  useEffect(() => {
    if (!params || !overviewReady || empty) return
    let live = true
    readerApi.page(id, params).then(
      (d) => {
        if (!live) return
        setData(d)
        setError(null)
      },
      (e: unknown) => {
        if (!live) return
        const pc = (e instanceof ApiError && (e.details as { page_count?: number } | undefined)?.page_count) || null
        if (pc && params.page > pc) goToPage(id, pc, true)
        else setError(e)
      },
    )
    return () => {
      live = false
    }
  }, [id, params, overviewReady, empty, pageTick])

  // Save progress a second after a page shows; it also tells us the page's
  // exact last line for the spoiler boundary.
  const shownPage = data?.page ?? null
  useEffect(() => {
    if (shownPage === null) return
    const size = prefs.chapterSize
    const t = setTimeout(() => {
      readerApi.saveProgress(id, shownPage, size).then(
        (p) => {
          setLastIdx({ page: shownPage, size, idx: p.last_line_idx })
          setOverview((o) => (o ? { ...o, percent_complete: p.percent_complete, last_page: p.last_page, last_line_idx: p.last_line_idx } : o))
        },
        () => {
          // No lines.edit permission or a transient error: reading still works.
        },
      )
    }, PROGRESS_DELAY_MS)
    return () => clearTimeout(t)
  }, [id, shownPage, prefs.chapterSize])

  const setPrefs = useCallback(
    (next: ReaderPrefs) => {
      if (page !== null && next.chapterSize !== prefs.chapterSize) {
        goToPage(id, keepPlace(page, prefs.chapterSize, next.chapterSize), true)
      }
      setPrefsState(next)
      savePrefs(browserStorage(), next)
    },
    [id, page, prefs.chapterSize],
  )

  const engine = engines.some((e) => e.name === enginePref)
    ? enginePref
    : (engines.find((e) => e.free) ?? engines[0])?.name ?? ''

  const known = lastIdx && lastIdx.page === page && lastIdx.size === prefs.chapterSize ? lastIdx.idx : null
  const boundary = page === null || !overview
    ? undefined
    : spoilerBoundary(prefs.spoilerFree, known, page, prefs.chapterSize, overview.line_count)

  // The label and frame show the move to another page until its HTML arrives.
  const pageLoading = page !== null && !error && data?.page !== page

  const onGo = (n: number) => {
    setResumed(null)
    goToPage(id, clampPage(n, count))
  }
  const workspaceHref = routeHref({ name: 'drama', id, stage: 'source' })
  const hasMedia = !!media && (media.original !== null || media.dub || media.narration)
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

  return (
    <div className={phone ? 'reader reader-phone' : 'reader'}>
      {phone ? (
        <header className="reader-phone-head">
          <a href={routeHref({ name: 'library' })} className="reader-back" aria-label="Back to Library">‹</a>
          <span className="reader-title">{title ?? 'Loading…'}</span>
          {aa}
        </header>
      ) : (
        <div className="reader-top">
          <nav aria-label="Breadcrumb" className="reader-crumbs">
            <a href={routeHref({ name: 'library' })}>Library</a>
            <span aria-hidden="true"> / </span>
            <a href={workspaceHref}>{title ?? 'Loading…'}</a>
          </nav>
          {aa}
        </div>
      )}

      {resumed !== null && (
        <div className="banner reader-note" role="status">
          <span>Resumed at page {resumed}.</span>
          <button type="button" className="link" onClick={() => setResumed(null)}>Dismiss</button>
        </div>
      )}
      <ErrorBanner error={error} />

      {empty ? (
        <section className="panel">
          <p>No lines to read yet.</p>
          <a href={workspaceHref}>Add lines on Source</a>
        </section>
      ) : (
        <>
          {hasMedia && media && <WatchSection dramaId={id} media={media} sourceLanguage={sourceLanguage} />}
          {!phone && page !== null && (
            <div className="reader-bar">
              <Pager page={page} count={count} loading={pageLoading} onGo={onGo} goTo />
              {metrics && <span className="muted" data-testid="reader-metrics">{metrics}</span>}
            </div>
          )}
          {data && (
            <iframe
              className={pageLoading ? 'reader-frame reader-frame-loading' : 'reader-frame'}
              title={`Page ${data.page} text`}
              sandbox="allow-scripts"
              referrerPolicy="no-referrer"
              srcDoc={data.html}
              aria-busy={pageLoading}
            />
          )}
          {!data && !error && <p className="muted">Loading…</p>}
          {/* After the page text, so the Sections don't jump down when it arrives. */}
          {page !== null && overview && data && (
            <div className="reader-sections">
              <WordsSection
                dramaId={id}
                page={page}
                chapterSize={prefs.chapterSize}
                engines={engines}
                engine={engine}
                onEngine={setEngine}
                onLookedUp={() => {
                  setPageTick((n) => n + 1)
                  setVocabTick((n) => n + 1)
                }}
              />
              {vocab && vocab.count > 0 && (
                <VocabSection dramaId={id} vocab={vocab} onChanged={() => setVocabTick((n) => n + 1)} />
              )}
              {terms.length > 0 && <GlossarySection terms={terms} />}
              <StorySection
                dramaId={id}
                page={page}
                chapterSize={prefs.chapterSize}
                boundary={boundary}
                spoilerFree={prefs.spoilerFree}
                engines={engines}
                engine={engine}
                onEngine={setEngine}
              />
              <NotesSection dramaId={id} />
            </div>
          )}
          {phone && page !== null && (
            <div className="reader-bottom">
              <Pager page={page} count={count} loading={pageLoading} onGo={onGo} goTo={false} />
            </div>
          )}
        </>
      )}
    </div>
  )
}
