import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent } from 'react'

import { createDrama, getContinueReading, getPresets, getRecent, getStats } from '../api/library'
import { listLines } from '../api/review'
import { coverUrl } from '../api/metadata'
import { listAllSeries } from '../api/series'
import type { DramaSummary } from '../api/types'
import { Badge } from '../components/Badge'
import { ButtonLink } from '../components/Button'
import { Card } from '../components/Card'
import { DramaDetailPanel } from '../components/DramaDetailPanel'
import { ErrorBanner } from '../components/ErrorBanner'
import { RetryBanner } from '../components/RetryBanner'
import { serverReachable } from '../chunkReload'
import { Field } from '../components/Field'
import { LibraryList } from '../components/LibraryList'
import { SameTitleNote } from '../components/SameTitleNote'
import { Section } from '../components/Section'
import { PageShell } from '../components/PageShell'
import { Sheet } from '../components/Sheet'
import {
  continueAction, continueItems, dramaName, needsCheckCount, tileHue, tileText, workspaceHref,
} from '../components/libraryView'
import { buttonClass } from '../components/uiClasses'
import { useLoad, type Loaded } from '../hooks/useLoad'
import { useHolds } from '../hooks/useHolds'
import { useMediaQuery } from '../hooks/useMediaQuery'
import { usePersistedState } from '../hooks/usePersistedState'
import { usePcOnly } from '../hooks/usePcOnly'
import { languageLabel, mediaTypeLabel } from '../labels'
import { routeHref } from '../router'
import { ADMIN_JOB_IDS } from '../types/libraryAdmin'
import { SelectionBar } from './libraryAdmin/SelectionBar'
import { pruneSelection, selectedItems } from './libraryAdmin/libraryAdmin'
import { useAdminJob } from './libraryAdmin/useAdminJob'
import type { DramaCreateRequest } from '../types/library'
import { mediaKind } from './workspace/detailsForm'
import {
  DEFAULT_PROFILE, MAX_SUMMARY_LEN, MEDIA_TYPES, NEW_SERIES, SOURCE_LANGUAGES, buildCreateRequest, chosenProfile, formForLink,
  createProblemField, deleteNotice, validateCreate, type CreateExtras,
} from './libraryForm'
import { GetStarted } from './libraryParity/GetStarted'
import { GET_STARTED_PREF, showGetStarted } from './libraryParity/getStartedLogic'
import {
  autofillHref, libraryHeadline, usageLine, usageSpent,
} from './libraryParity/libraryParity'
import './libraryParity/libraryParity.css'
import { skippedNote } from './workspace/translateForm'
import { SERIES_HELP } from '../helpText'
import { NewTitleLink } from './NewTitleLink'
import { createdFromLinkNote } from './linkFill'
import { ADD_LINK_ID, AddBox } from './makeSubtitles/AddBox'
import { clearHandoff, peekHandoff, type NewTitleStart } from './makeSubtitles/addBoxModel'
import { mediaTypeFor, titleFor } from './makeSubtitles/makeSubtitlesFlow'



// How many things the shelf last had to continue (0: nothing), so its placeholder has the shelf's shape.
const CONTINUE_COUNT_PREF = 'library.continue.count'

const NO_ROWS: ReadonlyMap<number, DramaSummary> = new Map()

// The reload an answer was asked in travels with it, so an answer that is older than the one shown never replaces it.
type CheckCount = { reload: number; n: number | null }

// Lines needing a look (the check queue the title page counts), for the tiles whose label names them. Asked once per
// title until the page reloads its data (Retry, Baihe back, a bulk action), which asks again so a stale count is
// replaced; until the new answer lands the old one stays, so a label and its link do not flicker to another step.
// A re-ask that fails leaves the count unknown rather than keep a number that could not be confirmed.
function useCheckCounts(ids: number[], reloadKey: number): ReadonlyMap<number, CheckCount> {
  const [counts, setCounts] = useState<ReadonlyMap<number, CheckCount>>(() => new Map())
  const asked = useRef({ reload: reloadKey, ids: new Set<number>() })
  const mounted = useRef(true)
  const key = ids.join(',')
  useEffect(() => {
    if (asked.current.reload !== reloadKey) asked.current = { reload: reloadKey, ids: new Set() }
    for (const id of key ? key.split(',').map(Number) : []) {
      if (asked.current.ids.has(id)) continue
      asked.current.ids.add(id)
      const keep = (n: number | null) => {
        if (!mounted.current) return
        setCounts((c) => ((c.get(id)?.reload ?? -1) > reloadKey ? c : new Map(c).set(id, { reload: reloadKey, n })))
      }
      listLines(id, 1, 1, 'risk').then((r) => keep(typeof r.risk_count === 'number' ? r.risk_count : null), () => keep(null))
    }
  }, [key, reloadKey])
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])
  return counts
}

// The shelf's card as Card draws it (Card cannot be hidden from a screen reader, so its markup is repeated here), with
// nothing in it and as many tiles as it last showed: it takes the loaded shelf's height under every width, density
// and text size as long as each title and button label fits on one line (a title that wraps makes its tile taller),
// and then the list below does not move when the shelf arrives.
function ShelfPlaceholder({ count, limit, phone }: { count: number; limit: number; phone: boolean }) {
  return (
    <section className="card continue-card continue-placeholder" aria-hidden="true">
      {!phone && (
        <header className="card-head">
          <div className="card-heading"><h2 className="card-title">Continue</h2></div>
        </header>
      )}
      <div className="card-body">
        <ul className="continue-list">
          {Array.from({ length: Math.min(count, limit) }, (_, i) => (
            <li key={i} className="continue-item">
              <div className="continue-row">
                <span className="continue-tile skeleton-block" />
                <div className="continue-text">
                  <span className="continue-title skeleton-text">{'\u00a0'}</span>
                  <span className="continue-meta skeleton-text short">{'\u00a0'}</span>
                </div>
                <span className={buttonClass('secondary', 'sm', 'continue-next skeleton-block')}>{'\u00a0'}</span>
              </div>
            </li>
          ))}
        </ul>
        {count > limit && <div className="actions"><span className={buttonClass('ghost', 'sm')}>{'\u00a0'}</span></div>}
      </div>
    </section>
  )
}

// "Continue": reading and workspace activity, one tap each on a button that names what comes next. Rendered
// only when there is something to continue.
function ContinueShelf({ continuing, recent, rows, rowsCurrent, phone, reloadKey }: {
  continuing: Loaded<{ items: Parameters<typeof continueItems>[0] }>
  recent: Loaded<{ items: Parameters<typeof continueItems>[1] }>
  /** The titles the Library list has shown since the page last loaded its data, or null until it has answered or
   *  failed: without a title's row a work tile can only say Open, so the shelf waits rather than show Open and then
   *  change. */
  rows: ReadonlyMap<number, DramaSummary> | null
  /** False while a reload waits for its title list to answer or fail: counts are asked only for titles that answer
   *  still puts in the check state, not on the rows it is about to replace. A list that fails leaves the old rows as
   *  all there is, so their counts are asked again. */
  rowsCurrent: boolean
  phone: boolean
  reloadKey: number
}) {
  const [all, setAll] = useState(false)
  const items = continueItems(continuing.data?.items ?? [], recent.data?.items ?? [])
  // The shelf is the tallest thing above the list; it holds its place until its own two requests and the title list
  // have answered, so the list does not jump down when they land.
  const loading = (!continuing.error && !recent.error && (continuing.data === null || recent.data === null)) || rows === null
  // The placeholder only shows for a viewer who had something to continue last time; for anyone else an empty shelf
  // would leave a gap that then collapses.
  const [storedCount, setStoredCount] = usePersistedState<number>(CONTINUE_COUNT_PREF, 0)
  const lastCount = storedCount > 0 ? Math.floor(storedCount) : 0
  // A failed request leaves `items` empty or partial, which says nothing about how many tiles the shelf has.
  const answered = !loading && !continuing.error && !recent.error
  useEffect(() => {
    if (answered) setStoredCount(items.length)
  }, [answered, items.length, setStoredCount])
  const limit = phone ? 1 : 4
  const shown = loading ? [] : all ? items : items.slice(0, limit)
  const row = (id: number) => rows?.get(id)
  const checking = rowsCurrent ? shown.filter((x) => needsCheckCount(x, row(x.dramaId))).map((x) => x.dramaId) : []
  const risk = useCheckCounts(checking, reloadKey)
  if (loading) return lastCount > 0 ? <ShelfPlaceholder count={lastCount} limit={limit} phone={phone} /> : null
  if (!items.length) return null
  return (
    <Card title={phone ? undefined : 'Continue'} className="continue-card" aria-label="Continue">
      <ul className="continue-list">
        {shown.map((x) => {
          const next = continueAction(x, row(x.dramaId), risk.get(x.dramaId)?.n ?? null)
          return (
            <li key={`${x.kind}-${x.dramaId}`} className="continue-item">
              <div className="continue-row">
                <span className={`continue-tile ${tileHue(x.dramaId)}`} aria-hidden="true">
                  {x.kind === 'read' && x.cover
                    ? <img src={coverUrl(x.dramaId)} alt="" loading="lazy" />
                    : tileText({ title_en: x.title, title_zh: x.titleZh })}
                </span>
                <div className="continue-text">
                  <span className="continue-title">{x.title}</span>
                  <span className="continue-meta">
                    {x.kind === 'read'
                      ? <>Reading{x.page ? ` · page ${x.page}` : ''}{x.percent != null && ` · ${Math.round(x.percent)}%`}</>
                      : <>{x.status && <Badge kind="status" value={x.status} />}{!x.status && <span>Workspace</span>}</>}
                  </span>
                </div>
                <ButtonLink size="sm" href={next.href} aria-label={`${next.label}: ${x.title}`} className="continue-next">
                  {next.label}
                </ButtonLink>
              </div>
            </li>
          )
        })}
      </ul>
      {items.length > limit && (
        <div className="actions">
          <button type="button" className={buttonClass('ghost', 'sm')} aria-expanded={all} onClick={() => setAll((v) => !v)}>
            {all ? 'Less' : `More (${items.length - limit})`}
          </button>
        </div>
      )}
    </Card>
  )
}


const NO_EXTRAS: CreateExtras = { series: '', newSeriesName: '', preset: DEFAULT_PROFILE }

// What has been typed into "New drama". The page keeps it, so closing the
// Sheet (Esc, backdrop, ×) and reopening it doesn't lose the input; only a
// successful create or Cancel clears it.
type CreateDraft = { form: DramaCreateRequest; extras: CreateExtras }

// The Add box's "Only add it" keeps what was typed and adds the link, or the title and type a file gives.
function draftFor(start: NonNullable<NewTitleStart>, draft: CreateDraft | null): CreateDraft {
  const extras = draft?.extras ?? NO_EXTRAS
  if ('link' in start) return { form: { ...(draft?.form ?? formForLink(start.link)), source_url: start.link }, extras }
  const form = draft?.form ?? formForLink('')
  const title = (form.title_en ?? '').trim() ? form.title_en : titleFor(start.fileName, '')
  return { form: { ...form, title_en: title, media_type: mediaTypeFor(start.fileName) }, extras }
}

// The "New drama" Sheet body. Language and type remember the last choice.
function CreateForm({ draft, onDraft, onCreated, onCancel, series, presets, fileName }: {
  draft: CreateDraft | null
  /** A file chosen in the Add box: the new title can't take it without a second pick, so the sheet says so. */
  fileName: string | null
  onDraft: (next: CreateDraft) => void
  onCreated: (id: number, autofill: boolean, note: string | null) => void
  onCancel: () => void
  series: Loaded<Awaited<ReturnType<typeof listAllSeries>>>
  presets: Loaded<Awaited<ReturnType<typeof getPresets>>>
}) {
  const [lastLanguage, setLastLanguage] = usePersistedState('library.newDrama.language', 'zh')
  const [lastType, setLastType] = usePersistedState('library.newDrama.mediaType', 'audio_drama')
  const form: DramaCreateRequest = draft?.form ?? {
    source_language: SOURCE_LANGUAGES.includes(lastLanguage) ? lastLanguage : 'zh',
    media_type: MEDIA_TYPES.includes(lastType) ? lastType : 'audio_drama',
    title_en: '', title_zh: '', author: '', studio: '', director: '', voice_actors: '', summary: '',
  }
  const extras = draft?.extras ?? NO_EXTRAS
  const setForm = (next: DramaCreateRequest) => onDraft({ form: next, extras })
  const setExtra = (k: keyof CreateExtras) => (e: { target: { value: string } }) =>
    onDraft({ form, extras: { ...extras, [k]: e.target.value } })
  const [error, setError] = useState<unknown>(null)
  const [invalid, setInvalid] = useState<{ message: string; field: 'source_language' | 'title_en' | null } | null>(null)
  const formRef = useRef<HTMLFormElement>(null)
  const [busy, setBusy] = useState(false)
  const profile = chosenProfile(extras.preset, presets.data?.items)
  const set = (k: keyof DramaCreateRequest) => (e: { target: { value: string } }) =>
    setForm({ ...form, [k]: e.target.value })

  const submit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    // "Create + fill with AI" goes on to the Source stage's auto-fill.
    const autofill = (e.nativeEvent as SubmitEvent).submitter?.getAttribute('name') === 'autofill'
    const body = buildCreateRequest(form, { ...extras, preset: profile })
    const problem = validateCreate(body)
    setInvalid(problem ? { message: problem, field: createProblemField(body) } : null)
    if (problem) return
    setBusy(true)
    createDrama(body).then(
      (d) => {
        setBusy(false)
        setLastLanguage(form.source_language)
        if (form.media_type) setLastType(form.media_type)
        const note = [d.title_from_link === 'placeholder' ? createdFromLinkNote(d.title_from_link) : '', skippedNote(d.profile_skipped).trim()]
          .filter(Boolean).join(' ')
        onCreated(d.id, autofill, note || null)
      },
      (err: unknown) => { setBusy(false); setError(err) },
    )
  }

  // After the errors render, so the field Field marks aria-invalid is the one that takes focus.
  useEffect(() => {
    if (invalid?.field) formRef.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus()
  }, [invalid])
  const fieldError = (name: 'source_language' | 'title_en') => (invalid?.field === name ? invalid.message : null)
  const kind = mediaKind(form.media_type)

  return (
    <form ref={formRef} onSubmit={submit} className="stack create-form" aria-label="New title">
      {fileName && (
        <p className="muted" role="note" data-testid="new-title-file-note">
          “{fileName}” is not attached. Create the title, then choose the file again in its Transcribe step.
        </p>
      )}
      <ErrorBanner error={presets.error} />
      {!!presets.data?.items.length && (
        <Field label="Profile" help="Your saved settings fill the new title. Language is still your choice; nothing starts by itself. Manage in Library tools.">
          <select value={profile} onChange={setExtra('preset')}>
            <option value="">No profile</option>
            {presets.data.items.map((p) => <option key={p.id} value={String(p.id)}>{p.name}{p.is_default ? ' (default)' : ''}</option>)}
          </select>
        </Field>
      )}
      <NewTitleLink
        form={form}
        onChange={(values) => setForm({ ...form, ...values })}
        onSeries={extras.series ? null : (name) => onDraft({ form, extras: { ...extras, series: NEW_SERIES, newSeriesName: name } })}
      />
      <Field label="English title" error={fieldError('title_en')}>
        {/* The attribute (not React's autoFocus) so the dialog's own focusing picks it. */}
        <input value={form.title_en} onChange={set('title_en')} ref={(el) => el?.setAttribute('autofocus', '')} />
      </Field>
      <Field label="Original title">
        <input value={form.title_zh} onChange={set('title_zh')} />
      </Field>
      <SameTitleNote titleEn={form.title_en} titleZh={form.title_zh} link={form.source_url} />
      <Field label="Stream / release date" help="Leave empty if unknown.">
        <input type="date" value={form.release_date ?? ''} onChange={set('release_date')} />
      </Field>
      <div className="field-row">
        <Field label="Source language" error={fieldError('source_language')}>
          <select value={form.source_language} onChange={set('source_language')}>
            {SOURCE_LANGUAGES.map((l) => <option key={l} value={l}>{languageLabel(l)}</option>)}
          </select>
        </Field>
        <Field label="Media type">
          <select value={form.media_type} onChange={set('media_type')}>
            {MEDIA_TYPES.map((m) => <option key={m} value={m}>{mediaTypeLabel(m)}</option>)}
          </select>
        </Field>
      </div>
      <Section title="Credits, summary and series" summary="Author, studio, director, voice actors, summary, series">
        <ErrorBanner error={series.error} />
        <div className="field-row">
          <Field label="Author">
            <input value={form.author} onChange={set('author')} />
          </Field>
          {kind !== 'novel' && (
            <Field label="Studio">
              <input value={form.studio} onChange={set('studio')} />
            </Field>
          )}
        </div>
        {kind === 'audio' && (
          <div className="field-row">
            <Field label="Director">
              <input value={form.director} onChange={set('director')} />
            </Field>
            <Field label="Voice actors" help="Comma-separated.">
              <input value={form.voice_actors} onChange={set('voice_actors')} />
            </Field>
          </div>
        )}
        <Field label="Summary" help="A short synopsis. You can edit it later in the workspace.">
          <textarea rows={3} maxLength={MAX_SUMMARY_LEN} value={form.summary ?? ''} onChange={set('summary')} />
        </Field>
        <div className="field-row">
          <Field label="Series" help={SERIES_HELP}>
            <select value={extras.series} onChange={setExtra('series')}>
              <option value="">No series</option>
              {series.data?.items.map((x) => <option key={x.id} value={String(x.id)}>{x.name}</option>)}
              <option value={NEW_SERIES}>New series…</option>
            </select>
          </Field>
          {extras.series === NEW_SERIES && (
            <Field label="New series name">
              <input value={extras.newSeriesName} onChange={setExtra('newSeriesName')} />
            </Field>
          )}
        </div>
      </Section>
      {invalid && !invalid.field && <p className="error" role="alert">{invalid.message}</p>}
      <ErrorBanner error={error} />
      <div className="actions sheet-actions">
        <button type="submit" className={buttonClass('primary')} disabled={busy}>Create title</button>
        <button type="submit" name="autofill" className={buttonClass('secondary')} disabled={busy}>Create + fill with AI</button>
        <button type="button" className={buttonClass('ghost')} disabled={busy} onClick={onCancel}>Discard draft</button>
      </div>
    </form>
  )
}

export default function LibraryPage() {
  const [selected, setSelected] = useState<{ id: number; title: string } | null>(null)
  // Handed over by the Add page, whose box has no New title sheet of its own.
  const [handoff] = useState(() => peekHandoff('new-title'))
  useEffect(() => clearHandoff(handoff), [handoff])
  const [creating, setCreating] = useState(handoff !== null)
  const [draft, setDraft] = useState<CreateDraft | null>(() => (handoff?.start ? draftFor(handoff.start, null) : null))
  const [draftFile, setDraftFile] = useState(() => (handoff?.start && 'fileName' in handoff.start ? handoff.start.fileName : null))
  // A title named after its link's website: stay here and say so, with the way to rename it.
  const [createdNote, setCreatedNote] = useState<{ id: number; text: string } | null>(null)
  const [reloadKey, setReloadKey] = useState(0)
  const [notice, setNotice] = useState<string | null>(null)
  const [items, setItems] = useState<DramaSummary[]>([])
  const [checked, setChecked] = useState<Set<number>>(() => new Set())
  const [selectMode, setSelectMode] = useState(false)
  const pc = usePcOnly()
  // POST /api/dramas needs library.create, which only an admin holds (at the PC or away). It only hides the
  // buttons: the open sheet must outlive a lost session so its draft survives signing back in.
  const canCreate = useHolds('library.create')
  const canLinkRun = useHolds('media.import_url')
  const [startedDismissed, setStartedDismissed] = usePersistedState(GET_STARTED_PREF, false)
  const phone = useMediaQuery('(max-width: 640px)')
  const stats = useLoad(getStats, reloadKey)
  const getStarted = showGetStarted(stats.data?.total_dramas, startedDismissed)
  const recent = useLoad(getRecent, reloadKey)
  const continuing = useLoad(getContinueReading, reloadKey)
  const continuingIds = useMemo(() => new Set((continuing.data?.items ?? []).map((x) => x.drama_id)), [continuing.data])
  const [seriesRetries, setSeriesRetries] = useState(0)
  const allSeries = useLoad(listAllSeries, reloadKey + seriesRetries * 1_000_000)
  const presets = useLoad(getPresets, reloadKey)
  // One export job for the selection bar.
  const exporter = useAdminJob(ADMIN_JOB_IDS.export, 'export')
  // The last bulk result stays after the bar closes (like `notice`).
  const [bulkResult, setBulkResult] = useState<string | null>(null)
  const reload = () => setReloadKey((k) => k + 1)
  // Four panels fail together when Baihe is down: say it once, and retry them all. A list failure remembers its
  // reload: the banner stays through the next reload until that reload's list answers, and the shelf must not take
  // the old failure for the new reload's answer. LibraryList drops an answer whose request a reload overtook, so a
  // failure it reports belongs to `reloadKey`.
  const [listFailure, setListFailure] = useState<{ reload: number; error: unknown } | null>(null)
  const listError = listFailure?.error ?? null
  const onLoadError = useCallback((error: unknown) => {
    setListFailure(error ? { reload: reloadKey, error } : null)
  }, [reloadKey])
  const loadError = stats.error ?? continuing.error ?? recent.error ?? listError
  useEffect(() => {
    if (!loadError) return
    const timer = window.setInterval(() => {
      void serverReachable().then((up) => up && reload())
    }, 5000)
    return () => window.clearInterval(timer)
  }, [loadError])

  // The titles the list has shown since the page last loaded its data. Answers of one load merge, so typing a search
  // that hides a title does not turn its Continue tile back into Open. The first answer after a reload replaces them;
  // until it arrives, and when that reload's list fails, the old rows stay, so the shelf does not flicker.
  // LibraryList drops an answer whose request a reload overtook, so every answer here belongs to `reloadKey`.
  const [seenRows, setSeenRows] = useState<{ reload: number; rows: ReadonlyMap<number, DramaSummary> } | null>(null)
  const onItems = useCallback((next: DramaSummary[]) => {
    setItems(next)
    setSeenRows((seen) => {
      const kept = seen && seen.reload === reloadKey ? seen.rows : NO_ROWS
      return { reload: reloadKey, rows: new Map([...kept, ...next.map((d) => [d.id, d] as const)]) }
    })
    // With nothing listed there is nothing to select.
    if (next.length === 0) setSelectMode(false)
  }, [reloadKey])
  // The selection holds only titles the viewer can see: a collapsed group, a filter or a reload drops the rest.
  const onVisible = useCallback((visible: DramaSummary[]) => setChecked((c) => pruneSelection(c, visible)), [])

  const openDetails = (id: number) => {
    const d = items.find((x) => x.id === id)
    setSelected({ id, title: d ? dramaName(d) : 'Title details' })
  }
  // A list that failed before it ever answered still lets the shelf show: its work tiles say Open.
  const shelfRows = seenRows?.rows ?? (listError ? NO_ROWS : null)

  // "Add a title by hand" (null) opens the sheet as New title always did, draft and all.
  const startNewTitle = (start: NewTitleStart) => {
    if (start) setDraft(draftFor(start, draft))
    setDraftFile(start && 'fileName' in start ? start.fileName : null)
    setCreating(true)
  }
  const addHref = routeHref({ name: 'make-subtitles' })
  // The empty list's Add: the box is just above it on a computer; a phone has no box on this page.
  const addFromEmpty = () => {
    if (phone) window.location.hash = addHref
    else document.getElementById(ADD_LINK_ID)?.focus()
  }

  const picked = selectedItems(items, checked)
  const clear = () => setChecked(new Set())
  const showBar = selectMode || (!phone && picked.length > 0)
  const bar = showBar && (
    <SelectionBar
      selected={picked}
      items={items}
      pc={pc}
      phone={phone}
      exporter={exporter}
      result={phone ? bulkResult : null}
      onResult={setBulkResult}
      onClear={clear}
      onDone={() => { setSelectMode(false); clear() }}
      onChanged={reload}
      onDeleted={(ids) => {
        if (selected && ids.includes(selected.id)) setSelected(null)
        setChecked((c) => new Set([...c].filter((id) => !ids.includes(id))))
      }}
    />
  )

  const dismiss = (onClick: () => void) => (
    <button type="button" className={buttonClass('ghost', 'sm')} onClick={onClick}>Dismiss</button>
  )
  const resultLine = bulkResult && (
    <p className="status-line" role="status" data-testid="bulk-result">
      <span>{bulkResult}</span>
      {dismiss(() => setBulkResult(null))}
    </p>
  )

  return (
    <PageShell
      width="wide"
      title="Library"
      className="library-page"
      meta={(
        <>
          <RetryBanner error={loadError} onRetry={reload} />
          {stats.data
            ? <p className="page-meta" data-testid="stats">{libraryHeadline(stats.data)}</p>
            : !stats.error && <p className="page-meta stats-placeholder" aria-hidden="true" />}
        </>
      )}
      headExtra={stats.data && usageSpent(stats.data.usage) && (
        <details className="stats-usage">
          <summary>{usageSpent(stats.data.usage)}</summary>
          <p className="page-meta" data-testid="stats-usage">{usageLine(stats.data.usage)}</p>
        </details>
      )}
      actions={(canCreate || canLinkRun) && (
        <>
          {canCreate && startedDismissed && stats.data?.total_dramas === 0 && (
            <button type="button" className={buttonClass('ghost', 'sm')} onClick={() => setStartedDismissed(false)}>Show first steps</button>
          )}
          {/* A phone opens the box on its own page, so the first title card stays on the first screen. */}
          {phone && <ButtonLink variant="primary" href={addHref}>Add</ButtonLink>}
        </>
      )}
    >
      {notice && (
        <p className="status-line warn" role="status" data-testid="delete-notice">
          <span>{notice}</span>
          {dismiss(() => setNotice(null))}
        </p>
      )}

      {createdNote && (
        <p className="banner" role="status">
          {createdNote.text} <a href={workspaceHref(createdNote.id)}>Open the title</a>{' '}
          <button type="button" className={buttonClass('ghost', 'sm')} onClick={() => setCreatedNote(null)}>Dismiss</button>
        </p>
      )}
      {getStarted && <GetStarted canCreate={canCreate} onHide={() => setStartedDismissed(true)} />}

      {!canCreate && stats.data?.total_dramas === 0 && !showGetStarted(0, startedDismissed) && (
        <p className="muted" data-testid="member-empty-note">
          No titles yet. Add some from <a href="#/sources">Sources</a> or <a href="#/discover">Discover</a>.
        </p>
      )}

      {/* Not on a phone: the box would push the first title card off the first screen; the header's Add opens it there. */}
      {(canCreate || canLinkRun) && !phone && <AddBox place="library" onNewTitle={startNewTitle} />}

      <ContinueShelf
        continuing={continuing} recent={recent} rows={shelfRows} rowsCurrent={seenRows?.reload === reloadKey || listFailure?.reload === reloadKey}
        phone={phone} reloadKey={reloadKey}
      />

      {!!allSeries.data?.items.length && (
        <p className="library-series-links" data-testid="library-series-links">
          <a href={routeHref({ name: 'series-list' })}>Series ({allSeries.data.items.length})</a>
        </p>
      )}

      {!phone && bar}
      {!(phone && bar) && resultLine}
      <LibraryList
        selectedId={selected?.id ?? null}
        onSelect={openDetails}
        reloadKey={reloadKey}
        checked={checked}
        onCheckedChange={setChecked}
        selectMode={selectMode}
        onSelectModeChange={setSelectMode}
        onItems={onItems}
        // While the first-steps card shows, it is the page's one call to action.
        onCreate={canCreate && !getStarted ? addFromEmpty : undefined}
        series={allSeries.data?.items ?? null}
        seriesError={!!allSeries.error}
        onRetrySeries={() => setSeriesRetries((n) => n + 1)}
        continuing={continuingIds}
        onVisible={onVisible}
        onLoadError={onLoadError}
      />
      {phone && bar}

      <Sheet open={creating} title="New title" onClose={() => setCreating(false)}>
        <CreateForm
          series={allSeries}
          presets={presets}
          draft={draft}
          fileName={draftFile}
          onDraft={setDraft}
          onCancel={() => { setDraft(null); setDraftFile(null); setCreating(false) }}
          onCreated={(id, autofill, note) => {
            setDraft(null)
            setDraftFile(null)
            setCreating(false)
            reload()
            if (note) {
              setCreatedNote({ id, text: note })
              return
            }
            // A new drama has nothing to review in a sheet: open its workspace
            // (Source has Details and credits).
            window.location.hash = autofill ? autofillHref(id) : workspaceHref(id)
          }}
        />
      </Sheet>
      <Sheet open={selected !== null} title={selected?.title ?? ''} onClose={() => setSelected(null)}>
        {selected && (
          <DramaDetailPanel
            key={selected.id}
            dramaId={selected.id}
            away={pc === 'remote'}
            onDeleted={pc === 'remote' && !items.find((d) => d.id === selected.id)?.made_by_me ? undefined : (r) => {
              setNotice(deleteNotice(r))
              setSelected(null)
              reload()
            }}
          />
        )}
      </Sheet>
    </PageShell>
  )
}
