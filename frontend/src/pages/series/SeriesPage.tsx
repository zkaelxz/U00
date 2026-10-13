import { useCallback, useState, type ReactNode } from 'react'

import {
  copyTerms, deleteSeries, getCopyTermsPreview, getSeriesDeletePreview, getSeriesPage, listAllSeries, renameSeries,
  type SeriesCopyTermsPreview, type SeriesDeletePreview, type SeriesPageData,
} from '../../api/series'
import { Badge } from '../../components/Badge'
import { ButtonLink } from '../../components/Button'
import { Card } from '../../components/Card'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { PageShell } from '../../components/PageShell'
import { Tabs } from '../../components/Tabs'
import { TypedConfirm } from '../../components/TypedConfirm'
import { dramaName, workspaceHref } from '../../components/libraryView'
import { tabButtonId, tabPanelId } from '../../components/tabIds'
import { buttonClass } from '../../components/uiClasses'
import { useHolds } from '../../hooks/useHolds'
import { useLoad } from '../../hooks/useLoad'
import { usePcOnly } from '../../hooks/usePcOnly'
import { usePersistedState } from '../../hooks/usePersistedState'
import { mediaTypeLabel } from '../../labels'
import { routeHref } from '../../router'
import {
  MAX_SERIES_NAME, SERIES_TABS, copyPreviewText, copyResultText, copySources, deleteBlocked, deleteLines, renameProblem,
  type SeriesTab,
} from './seriesModel'
import { DefaultsTab } from './DefaultsTab'
import { TITLE_SORTS, defaultSeriesSort, isTitleSort, sortTitles, type TitleSort } from '../titleOrder'
import './seriesPage.css'

const glossaryHref = (dramaId: number) => routeHref({ name: 'drama', id: dramaId, stage: 'translate', tab: 'glossary' })

const lineCount = (n: number) => (n === 1 ? '1 line' : `${n} lines`)

function TitlesTab({ data }: { data: SeriesPageData }) {
  // Remembered per series in this browser (no server state). Until the viewer picks one:
  // oldest first once any title has a date, else the order the server sends.
  const [stored, setStoredSort] = usePersistedState(`series.sort.v1.${data.id}`, '')
  const sort: TitleSort = isTitleSort(stored) ? stored : defaultSeriesSort(data.titles)
  if (!data.titles.length) {
    return (
      <p className="muted" data-testid="series-no-titles">
        No titles in this series yet. Put a title in it from the Series picker in that title's Glossary.
      </p>
    )
  }
  const ordered = sortTitles(data.titles, sort)
  return (
    <>
    <Field label="Sort titles by">
      <select value={sort} onChange={(e) => setStoredSort(e.target.value)} data-testid="series-sort">
        {TITLE_SORTS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
      </select>
    </Field>
    <ul className="series-titles" data-testid="series-titles">
      {ordered.map((t) => (
        <li key={t.id} className="series-title-row">
          <div className="series-title-text">
            <a className="table-title" href={workspaceHref(t.id)}>
              {t.episode_number != null && <span className="muted">#{t.episode_number} </span>}
              {dramaName(t)}
            </a>
            {t.title_en && t.title_zh && <span className="muted">{t.title_zh}</span>}
          </div>
          <span className="series-title-meta">
            {t.release_date && <>{t.release_date} · </>}{mediaTypeLabel(t.media_type)} · {lineCount(t.line_count)}
            {t.status && <> <Badge kind="status" value={t.status} /></>}
          </span>
        </li>
      ))}
    </ul>
    </>
  )
}

function CopyTermsCard({ data, onCopied }: { data: SeriesPageData; onCopied: () => void }) {
  const all = useLoad(listAllSeries, 0)
  const sources = copySources(all.data?.items ?? [], data.id)
  const [source, setSource] = useState('')
  const [preview, setPreview] = useState<SeriesCopyTermsPreview | null>(null)
  const [result, setResult] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)

  const run = <T,>(p: Promise<T>, done: (v: T) => void) => {
    setBusy(true)
    setError(null)
    p.then(
      (v) => { setBusy(false); done(v) },
      (e: unknown) => { setBusy(false); setError(e) },
    )
  }
  const pick = (v: string) => { setSource(v); setPreview(null); setResult(null); setError(null) }

  return (
    <Card title="Copy terms from another series" className="series-copy-card">
      <p className="muted">
        Adds the other series' glossary terms to this one. A term this series already has (same original text, ignoring
        case and width) is skipped and keeps its own translation and notes. Nothing is removed from the other series.
      </p>
      {!data.can_edit ? (
        <p className="muted">You can't change this series' glossary.</p>
      ) : (
        <>
          <ErrorBanner error={all.error} />
          {all.data && !sources.length ? (
            <p className="muted">No other series you can see has glossary terms.</p>
          ) : (
            <div className="series-copy-row">
              <Field label="Copy from">
                <select value={source} disabled={busy || !all.data} onChange={(e) => pick(e.target.value)}>
                  <option value="">Pick a series…</option>
                  {sources.map((s) => (
                    <option key={s.id} value={String(s.id)}>{s.name} ({s.glossary_term_count})</option>
                  ))}
                </select>
              </Field>
              <button
                type="button"
                className={buttonClass('secondary')}
                disabled={busy || !source}
                onClick={() => run(getCopyTermsPreview(data.id, Number(source)), (p) => { setPreview(p); setResult(null) })}
              >
                Preview
              </button>
            </div>
          )}
          {preview && (
            <div className="series-copy-preview" data-testid="series-copy-preview">
              <p role="status">{copyPreviewText(preview)}</p>
              {preview.new_terms.length > 0 && (
                <details>
                  <summary>Terms to add</summary>
                  <p className="series-term-list">{preview.new_terms.join(' · ')}{preview.new_count > preview.new_terms.length && ' …'}</p>
                </details>
              )}
              {preview.new_count > 0 && (
                <div className="actions">
                  <button
                    type="button"
                    className={buttonClass('primary')}
                    disabled={busy}
                    onClick={() => run(copyTerms(data.id, preview.source_series_id), (r) => {
                      setPreview(null)
                      setResult(copyResultText(r))
                      onCopied()
                    })}
                  >
                    {busy ? 'Working…' : `Copy ${preview.new_count === 1 ? '1 term' : `${preview.new_count} terms`}`}
                  </button>
                  <button type="button" className={buttonClass('ghost')} disabled={busy} onClick={() => setPreview(null)}>Cancel</button>
                </div>
              )}
            </div>
          )}
          {result && <p className="status-line" role="status" data-testid="series-copy-result">{result}</p>}
          <ErrorBanner error={error} onDismiss={() => setError(null)} />
        </>
      )}
    </Card>
  )
}

function GlossaryTab({ data, onCopied }: { data: SeriesPageData; onCopied: () => void }) {
  const first = data.titles[0]
  return (
    <>
      <Card title="Glossary and instructions">
        <p>
          {data.glossary_term_count === 1 ? '1 glossary term' : `${data.glossary_term_count} glossary terms`} ·{' '}
          {data.has_instructions ? 'series instructions set' : 'no series instructions'}
        </p>
        {first ? (
          <div className="actions">
            <ButtonLink size="sm" href={glossaryHref(first.id)}>Open glossary and instructions</ButtonLink>
            <span className="muted">Edited from any title in the series; shared by all of them.</span>
          </div>
        ) : (
          <p className="muted">Add a title to this series to edit its glossary and instructions.</p>
        )}
      </Card>
      <CopyTermsCard data={data} onCopied={onCopied} />
      <p className="muted" data-testid="series-no-series-hint">
        A title with no series can't hold glossary terms. To give one these terms, open that title's Glossary and pick
        this series (or “New series…”) in its Series picker.
      </p>
    </>
  )
}

function RenameCard({ data, onRenamed }: { data: SeriesPageData; onRenamed: () => void }) {
  const all = useLoad(listAllSeries, 0)
  const [name, setName] = useState(data.name)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const others = (all.data?.items ?? []).filter((s) => s.id !== data.id)
  const problem = renameProblem(name, data.name, others)
  const dirty = name.trim() !== data.name

  return (
    <Card title="Rename series">
      {!data.can_edit ? (
        <p className="muted">You can't rename this series.</p>
      ) : (
        <form
          onSubmit={(e) => {
            e.preventDefault()
            if (problem) return
            setBusy(true)
            renameSeries(data.id, name).then(
              () => { setBusy(false); setError(null); onRenamed() },
              (err: unknown) => { setBusy(false); setError(err) },
            )
          }}
        >
          <Field label="Series name" error={dirty && problem ? problem : undefined}>
            <input value={name} maxLength={MAX_SERIES_NAME} disabled={busy} onChange={(e) => setName(e.target.value)} />
          </Field>
          <div className="actions">
            <button type="submit" className={buttonClass('secondary')} disabled={busy || !!problem}>
              {busy ? 'Working…' : 'Rename series'}
            </button>
          </div>
          <ErrorBanner error={error} onDismiss={() => setError(null)} />
        </form>
      )}
    </Card>
  )
}

function DeleteCard({ data }: { data: SeriesPageData }) {
  const pc = usePcOnly()
  const [preview, setPreview] = useState<SeriesDeletePreview | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const blocked = deleteBlocked(preview, pc)

  const open = () => {
    setBusy(true)
    setError(null)
    getSeriesDeletePreview(data.id).then(
      (p) => { setBusy(false); setPreview(p) },
      (e: unknown) => { setBusy(false); setError(e) },
    )
  }
  const remove = () => {
    if (!preview) return
    setBusy(true)
    deleteSeries(data.id, preview.needs_typed_confirm).then(
      () => { window.location.hash = routeHref({ name: 'library' }) },
      (e: unknown) => { setBusy(false); setError(e) },
    )
  }
  const lines = preview && deleteLines(preview)

  return (
    <Card title="Delete series">
      {pc === 'remote' ? (
        <p className="muted">{blocked}</p>
      ) : !preview ? (
        <div className="actions">
          <button type="button" className={buttonClass('danger', 'sm')} disabled={busy} onClick={open}>
            Delete series…
          </button>
        </div>
      ) : (
        <div className="series-delete-prompt" data-testid="series-delete-prompt">
          {lines && lines.kept && <p>{lines.kept}</p>}
          {lines && lines.deleted.length > 0 ? (
            <>
              <p>Deleted with the series:</p>
              <ul>{lines.deleted.map((l) => <li key={l}>{l}</li>)}</ul>
            </>
          ) : (
            <p>The series is empty: nothing else is deleted.</p>
          )}
          <p className="muted">No files are touched.</p>
          {preview.needs_typed_confirm ? (
            <TypedConfirm
              word="DELETE"
              exact
              action="Delete series"
              busy={busy}
              blocked={blocked}
              onConfirm={remove}
              onCancel={() => { setPreview(null); setError(null) }}
            />
          ) : (
            <>
              {blocked && <p className="muted">{blocked}</p>}
              <ConfirmButton name={data.name} label="Delete series" busy={busy} disabled={!!blocked} onConfirm={remove} />
            </>
          )}
        </div>
      )}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </Card>
  )
}

export default function SeriesPage({ id, tab: initialTab }: { id: number; tab?: SeriesTab }) {
  const [reloadKey, setReloadKey] = useState(0)
  const load = useCallback(() => getSeriesPage(id), [id])
  const page = useLoad(load, reloadKey)
  const [tab, setTab] = useState<SeriesTab>(initialTab ?? 'titles')
  const canEdit = useHolds('lines.edit')
  const reload = () => setReloadKey((k) => k + 1)
  const data = page.data
  const select = (t: SeriesTab) => {
    setTab(t)
    window.history.replaceState(null, '', routeHref({ name: 'series', id, tab: t }))
  }

  const panel = (t: SeriesTab, body: ReactNode) => (
    <div key={t} id={tabPanelId('series', t)} role="tabpanel" aria-labelledby={tabButtonId('series', t)} hidden={tab !== t}>
      {tab === t && body}
    </div>
  )
  const shown = data && !canEdit ? { ...data, can_edit: false } : data

  return (
    <PageShell
      as="main"
      width="standard"
      className="series-page"
      title={data?.name ?? 'Series'}
      back={(
        <nav aria-label="Breadcrumb" className="reader-crumbs">
          <a href={routeHref({ name: 'library' })}>Library</a>
          <span aria-hidden="true"> / </span>
          <span>Series</span>
        </nav>
      )}
      meta={data && (
        <p className="page-meta" data-testid="series-meta">
          {data.titles.length === 1 ? '1 title' : `${data.titles.length} titles`} ·{' '}
          {data.glossary_term_count === 1 ? '1 glossary term' : `${data.glossary_term_count} glossary terms`} ·{' '}
          {data.character_count === 1 ? '1 series character' : `${data.character_count} series characters`}
          {data.is_private && <> <Badge>Private</Badge></>}
        </p>
      )}
      tabs={data && <Tabs label="Series" idPrefix="series" tabs={SERIES_TABS} active={tab} onSelect={select} />}
    >
      <ErrorBanner error={page.error} />
      {!data && !page.error && <p className="muted">Loading…</p>}
      {shown && (
        <>
          {panel('titles', <TitlesTab key={shown.id} data={shown} />)}
          {panel('defaults', <DefaultsTab data={shown} />)}
          {panel('glossary', <GlossaryTab data={shown} onCopied={reload} />)}
          {panel('settings', (
            <>
              <RenameCard key={shown.name} data={shown} onRenamed={reload} />
              <DeleteCard data={shown} />
            </>
          ))}
        </>
      )}
    </PageShell>
  )
}
