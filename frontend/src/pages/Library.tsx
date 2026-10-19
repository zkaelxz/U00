import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'

import { createDrama, getContinueReading, getPresets, getRecent, getStats } from '../api/library'
import { coverUrl } from '../api/metadata'
import { listAllSeries } from '../api/series'
import type { DramaSummary } from '../api/types'
import { Badge } from '../components/Badge'
import { ButtonLink } from '../components/Button'
import { Card } from '../components/Card'
import { DramaDetailPanel } from '../components/DramaDetailPanel'
import { ErrorBanner } from '../components/ErrorBanner'
import { Field } from '../components/Field'
import { LibraryList } from '../components/LibraryList'
import { Section } from '../components/Section'
import { PageShell } from '../components/PageShell'
import { Sheet } from '../components/Sheet'
import {
  continueItems, dramaName, readHref, tileHue, tileText, workspaceHref, type ContinueItem,
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
  DEFAULT_PROFILE, MAX_SUMMARY_LEN, MEDIA_TYPES, NEW_SERIES, SOURCE_LANGUAGES, buildCreateRequest, chosenProfile,
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



const CONTINUE_SEEN_PREF = 'library.continue.hadItems'

// "Continue": reading and workspace activity, one Resume tap each. Rendered
// only when there is something to resume.
function ContinueShelf({ continuing, recent, mediaTypes, phone }: {
  continuing: Loaded<{ items: Parameters<typeof continueItems>[0] }>
  recent: Loaded<{ items: Parameters<typeof continueItems>[1] }>
  mediaTypes: Map<number, string | null>
  phone: boolean
}) {
  const [all, setAll] = useState(false)
  const items = continueItems(continuing.data?.items ?? [], recent.data?.items ?? [])
  const errors = <><ErrorBanner error={continuing.error} /><ErrorBanner error={recent.error} /></>
  // The shelf is the tallest thing above the list; holding its place until the
  // two requests settle keeps the list from jumping down when they land.
  const loading = !continuing.error && !recent.error && (continuing.data === null || recent.data === null)
  // Only reserve the space for a viewer who had something to continue last
  // time; otherwise an empty shelf would leave a 140 px gap that then collapses.
  const [hadItems, setHadItems] = usePersistedState(CONTINUE_SEEN_PREF, false)
  useEffect(() => {
    if (!loading) setHadItems(items.length > 0)
  }, [loading, items.length, setHadItems])
  if (loading) return hadItems ? <div className="continue-skeleton skeleton-block" aria-hidden="true" /> : null
  if (!items.length) return errors
  const limit = phone ? 1 : 4
  const shown = all ? items : items.slice(0, limit)
  const href = (x: ContinueItem) =>
    x.kind === 'read'
      ? readHref({ id: x.dramaId, media_type: mediaTypes.get(x.dramaId) ?? null })
      : workspaceHref(x.dramaId)
  return (
    <Card title={phone ? undefined : 'Continue'} className="continue-card" aria-label="Continue">
      {errors}
      <ul className="continue-list">
        {shown.map((x) => (
          <li key={`${x.kind}-${x.dramaId}`} className="continue-item">
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
            <ButtonLink
              size="sm"
              href={href(x)}
              aria-label={`Resume ${x.kind === 'read' ? 'reading' : 'work on'} ${x.title}`}
            >
              Resume
            </ButtonLink>
          </li>
        ))}
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

// The "New drama" Sheet body. Language and type remember the last choice.
function CreateForm({ draft, onDraft, onCreated, onCancel, series, presets }: {
  draft: CreateDraft | null
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
    // "Create and fill details with AI" goes on to the Source stage's auto-fill.
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
      <ErrorBanner error={presets.error} />
      {!!presets.data?.items.length && (
        <Field label="Profile" help="Your saved translation and transcription settings. The new title starts with them already filled in, so you can go straight on. The language stays your choice below, and nothing starts by itself. Manage profiles in Library tools.">
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
        <button type="submit" name="autofill" className={buttonClass('secondary')} disabled={busy}>Create and fill details with AI</button>
        <button type="button" className={buttonClass('ghost')} disabled={busy} onClick={onCancel}>Discard draft</button>
      </div>
    </form>
  )
}

export default function LibraryPage() {
  const [selected, setSelected] = useState<{ id: number; title: string } | null>(null)
  const [creating, setCreating] = useState(false)
  const [draft, setDraft] = useState<CreateDraft | null>(null)
  // A title named after its link's website: stay here and say so, with the way to rename it.
  const [createdNote, setCreatedNote] = useState<{ id: number; text: string } | null>(null)
  const [reloadKey, setReloadKey] = useState(0)
  const [notice, setNotice] = useState<string | null>(null)
  const [items, setItems] = useState<DramaSummary[]>([])
  const [checked, setChecked] = useState<Set<number>>(() => new Set())
  const [selectMode, setSelectMode] = useState(false)
  const pc = usePcOnly()
  // POST /api/dramas needs admin.library, which household members lack. It only hides the
  // buttons: the open sheet must outlive a lost session so its draft survives signing back in.
  const canCreate = useHolds('admin.library')
  const [startedDismissed, setStartedDismissed] = usePersistedState(GET_STARTED_PREF, false)
  const phone = useMediaQuery('(max-width: 640px)')
  const stats = useLoad(getStats, reloadKey)
  const getStarted = showGetStarted(stats.data?.total_dramas, startedDismissed)
  // Making subtitles is the one primary action until the first title exists.
  const emptyLibrary = stats.data?.total_dramas === 0
  const recent = useLoad(getRecent, reloadKey)
  const continuing = useLoad(getContinueReading, reloadKey)
  const [seriesRetries, setSeriesRetries] = useState(0)
  const allSeries = useLoad(listAllSeries, reloadKey + seriesRetries * 1_000_000)
  const presets = useLoad(getPresets, reloadKey)
  // One export job for the selection bar.
  const exporter = useAdminJob(ADMIN_JOB_IDS.export, 'export')
  // The last bulk result stays after the bar closes (like `notice`).
  const [bulkResult, setBulkResult] = useState<string | null>(null)
  const reload = () => setReloadKey((k) => k + 1)

  // With nothing listed there is nothing to select.
  const onItems = useCallback((next: DramaSummary[]) => {
    setItems(next)
    if (next.length === 0) setSelectMode(false)
  }, [])
  // The selection holds only titles the viewer can see: a collapsed group, a filter or a reload drops the rest.
  const onVisible = useCallback((visible: DramaSummary[]) => setChecked((c) => pruneSelection(c, visible)), [])

  const openDetails = (id: number) => {
    const d = items.find((x) => x.id === id)
    setSelected({ id, title: d ? dramaName(d) : 'Title details' })
  }
  const mediaTypes = new Map(items.map((d) => [d.id, d.media_type]))

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
          <ErrorBanner error={stats.error} />
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
      actions={canCreate && (
        <>
          {!getStarted && <ButtonLink variant={emptyLibrary ? 'primary' : 'ghost'} size="sm" href={routeHref({ name: 'make-subtitles' })}>Make subtitles</ButtonLink>}
          {startedDismissed && stats.data?.total_dramas === 0 && (
            <button type="button" className={buttonClass('ghost', 'sm')} onClick={() => setStartedDismissed(false)}>Show first steps</button>
          )}
          <button type="button" className={buttonClass(emptyLibrary ? 'secondary' : 'primary')} onClick={() => setCreating(true)}>New title</button>
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

      <ContinueShelf continuing={continuing} recent={recent} mediaTypes={mediaTypes} phone={phone} />

      {!!allSeries.data?.items.length && (
        <details className="library-series-links" data-testid="library-series-links">
          <summary>Series ({allSeries.data.items.length})</summary>
          <ul>
            {allSeries.data.items.map((s) => (
              <li key={s.id}>
                <a href={routeHref({ name: 'series', id: s.id })}>{s.name}</a>
                <span className="muted"> · {s.title_count === 1 ? '1 title' : `${s.title_count} titles`}</span>
              </li>
            ))}
          </ul>
        </details>
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
        onCreate={canCreate && !getStarted ? () => setCreating(true) : undefined}
        series={allSeries.data?.items ?? null}
        seriesError={!!allSeries.error}
        onRetrySeries={() => setSeriesRetries((n) => n + 1)}
        onVisible={onVisible}
      />
      {phone && bar}

      <Sheet open={creating} title="New title" onClose={() => setCreating(false)}>
        <CreateForm
          series={allSeries}
          presets={presets}
          draft={draft}
          onDraft={setDraft}
          onCancel={() => { setDraft(null); setCreating(false) }}
          onCreated={(id, autofill, note) => {
            setDraft(null)
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
            onDeleted={pc === 'remote' ? undefined : (r) => {
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
