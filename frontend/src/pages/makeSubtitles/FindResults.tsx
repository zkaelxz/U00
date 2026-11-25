/** Find & Add: the title search under the box. A name searches the enabled Sources; each match can be added to the Library in one tap, or opened in Sources, where chapters are picked and imported. */
import { useEffect, useState } from 'react'

import { createDrama, titlesByLinks } from '../../api/library'
import { listSources, SEARCH_JOB_ID, startSearch } from '../../api/sources'
import { ErrorBanner } from '../../components/ErrorBanner'
import { buttonClass } from '../../components/uiClasses'
import { ButtonLink } from '../../components/Button'
import { useHolds } from '../../hooks/useHolds'
import { usePersistedState } from '../../hooks/usePersistedState'
import { usePcOnly } from '../../hooks/usePcOnly'
import { useReadiness } from '../../hooks/useReadiness'
import { languageLabel } from '../../labels'
import { routeHref } from '../../router'
import type { SearchResult, SourceSummary } from '../../types/sources'
import { browserTierNeed } from '../readiness/needs'
import { SourceErrorLine } from '../sources/SearchPanel'
import { RESULTS_PAGE, describeSourceError, resultsHeader, searchSourcesParam, searchableSources, selectedSources, sourceProgressPercent } from '../sources/sourcesFormat'
import { useSourcesJob } from '../sources/useSourcesJob'
import { newDramaRequest } from '../sources/urlImportFormat'
import { canAddToLibrary, findRows, FIND_FILTERS, type FindFilter, type FindRow } from './findResultsModel'

const LOOKUP_CHUNK = 60

/** `n` changes with every press of Search, so the same name can be searched again. */
export type FindRequest = { query: string; n: number }

export function FindResults({ request }: { request: FindRequest | null }) {
  const remote = usePcOnly() === 'remote'
  const job = useSourcesJob<SearchResult>(SEARCH_JOB_ID)
  const { readiness } = useReadiness()
  const [sources, setSources] = useState<SourceSummary[] | null>(null)
  const [loadError, setLoadError] = useState<unknown>(null)
  // The same ticks as the Sources page's "Search in", so a choice made there holds here.
  const [excluded] = usePersistedState<string[]>('sources.searchIn', [])
  const [shown, setShown] = useState(RESULTS_PAGE)
  const [filter, setFilter] = useState<FindFilter>('all')
  const canCreate = useHolds('library.create')
  // The title each row's link already has in the Library (null: none), looked up for the rows on screen.
  const [titleIds, setTitleIds] = useState<Record<string, number | null>>({})
  const [adding, setAdding] = useState<string | null>(null)
  const [addError, setAddError] = useState<unknown>(null)

  useEffect(() => { listSources().then(setSources, setLoadError) }, [])

  const names = sources ? searchableSources(sources, !!browserTierNeed(readiness)).map((s) => s.name) : []
  const selected = selectedSources(names, Array.isArray(excluded) ? excluded : [])
  const display = (name: string) => sources?.find((s) => s.name === name)?.display_name ?? name

  const q = request?.query ?? null
  const n = request?.n ?? 0
  const ready = sources !== null
  useEffect(() => {
    if (!q || !ready || names.length === 0) return
    setShown(RESULTS_PAGE)
    job.start(() => startSearch(q, searchSourcesParam(names, selected)))
    // A new press of Search is the only trigger; the source lists settle before the first one.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [n, ready])

  const done = job.status === 'done' ? job.result : null
  const allRows = done && sources ? findRows(done.results, sources, filter) : []
  const rows = allRows.slice(0, shown)
  const lookupKey = rows.map((r) => r.key).join('|')
  useEffect(() => {
    const asked = rows.filter((r) => r.url && !(r.key in titleIds))
    if (asked.length === 0) return
    let live = true
    // The server takes 60 links a call; a filter change after "Show more" can ask for more.
    for (let i = 0; i < asked.length; i += LOOKUP_CHUNK) {
      const chunk = asked.slice(i, i + LOOKUP_CHUNK)
      titlesByLinks(chunk.map((r) => r.url)).then(
        ({ title_ids }) => { if (live) setTitleIds((cur) => ({ ...cur, ...Object.fromEntries(chunk.map((r, j) => [r.key, title_ids[j] ?? null])) })) },
        () => undefined, // Without the lookup every row simply offers Add to Library.
      )
    }
    return () => { live = false }
    // rows is rebuilt each render; lookupKey is what changes with the rows shown.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lookupKey])

  function add(row: FindRow) {
    setAdding(row.key)
    setAddError(null)
    createDrama({ ...newDramaRequest(row.title, row.language, row.kind === 'comics'), source_url: row.url, translate_title: true }).then(
      (d) => { setAdding(null); setTitleIds((cur) => ({ ...cur, [row.key]: d.id })) },
      (e: unknown) => { setAdding(null); setAddError(e) },
    )
  }

  if (!request) return null
  const running = job.status === 'running'
  const result = job.status === 'done' ? job.result : null
  const errors = result ? Object.entries(result.errors ?? {}) : []

  return (
    <div className="find-results" aria-live="polite">
      <ErrorBanner error={loadError} />
      {ready && names.length === 0 && (
        <p className="muted">No source can search yet. Turn one on in <a href={routeHref({ name: 'sources', tab: 'manage' })}>Manage sources</a>{remote ? ' on the main PC' : ''}.</p>
      )}
      <ErrorBanner error={job.startError} onDismiss={job.clearStartError} describe={{ serverText: true }} />
      {running && (
        <p>
          Searching…{sourceProgressPercent(job.progress)} ·{' '}
          <button type="button" className={buttonClass('ghost', 'sm')} onClick={job.cancel}>Cancel</button>
        </p>
      )}
      {running && <p className="muted">Each source is paced, so this can take a minute.</p>}
      {job.status === 'error' && job.error && <SourceErrorLine copy={describeSourceError(job.error, 'The search', remote)} />}
      {result && (
        <div data-testid="search-results">
          <p><strong>{resultsHeader(result.results.length, errors.length)}</strong></p>
          {result.cancelled && <p className="muted">Stopped. Showing what came back before you cancelled.</p>}
          {!result.results.length && <p className="muted">No results for “{result.query}”. Try the name in its own language, or paste a link to its page.</p>}
          {errors.map(([name, err]) => <SourceErrorLine key={name} copy={describeSourceError(err, display(name), remote)} />)}
          <div className="find-filters" role="group" aria-label="Show">
            {FIND_FILTERS.map((f) => (
              <button key={f.id} type="button" className={buttonClass(filter === f.id ? 'primary' : 'secondary', 'sm')} aria-pressed={filter === f.id}
                onClick={() => setFilter(f.id)}>{f.label}</button>
            ))}
          </div>
          <ErrorBanner error={addError} onDismiss={() => setAddError(null)} />
          {rows.length === 0 && result.results.length > 0 && <p className="muted">Nothing of that kind in these results.</p>}
          <ul className="source-results">
            {rows.map((r) => {
              const inLibrary = titleIds[r.key]
              return (
                <li key={r.key}>
                  <span className="source-result-text">
                    <strong>{r.title}</strong>
                    <span className="muted">{r.kindWord}{r.language ? ` · ${languageLabel(r.language)}` : ''} · from {r.sourceName}</span>
                    {inLibrary != null && <span className="find-in-library">In your Library</span>}
                  </span>
                  <div className="actions">
                    {inLibrary != null ? (
                      <ButtonLink size="sm" variant="secondary" href={routeHref({ name: 'drama', id: inLibrary, stage: null })}>Open</ButtonLink>
                    ) : canCreate && canAddToLibrary(r) ? (
                      <button type="button" className={buttonClass('primary', 'sm')} disabled={adding !== null} onClick={() => add(r)}>
                        {adding === r.key ? 'Adding…' : 'Add to Library'}
                      </button>
                    ) : null}
                    <ButtonLink size="sm" variant={inLibrary != null || (canCreate && canAddToLibrary(r)) ? 'ghost' : 'secondary'}
                      href={routeHref({ name: 'sources', open: { source: r.source, series_id: r.seriesId, title: r.title } })}>
                      Chapters
                    </ButtonLink>
                  </div>
                </li>
              )
            })}
          </ul>
          {allRows.length > shown && (
            <button type="button" className={buttonClass('secondary')} onClick={() => setShown((c) => c + RESULTS_PAGE)}>
              Show {Math.min(RESULTS_PAGE, allRows.length - shown)} more
            </button>
          )}
        </div>
      )}
    </div>
  )
}
