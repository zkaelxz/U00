/** Find & Add: the title search under the box. A name searches the enabled Sources; each match opens its series in Sources, where chapters are picked and imported. */
import { useEffect, useState } from 'react'

import { listSources, SEARCH_JOB_ID, startSearch } from '../../api/sources'
import { ErrorBanner } from '../../components/ErrorBanner'
import { buttonClass } from '../../components/uiClasses'
import { ButtonLink } from '../../components/Button'
import { usePersistedState } from '../../hooks/usePersistedState'
import { usePcOnly } from '../../hooks/usePcOnly'
import { useReadiness } from '../../hooks/useReadiness'
import { routeHref } from '../../router'
import type { SearchResult, SourceSummary } from '../../types/sources'
import { browserTierNeed } from '../readiness/needs'
import { SourceErrorLine } from '../sources/SearchPanel'
import { RESULTS_PAGE, describeSourceError, resultsHeader, searchSourcesParam, searchableSources, selectedSources, sourceProgressPercent } from '../sources/sourcesFormat'
import { useSourcesJob } from '../sources/useSourcesJob'

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
          <ul className="source-results">
            {result.results.slice(0, shown).map((m) => (
              <li key={m.key}>
                <span className="source-result-text">
                  <strong>{m.title}</strong>
                  <span className="muted">on {m.sources.map(display).join(' · ')}</span>
                </span>
                <div className="actions">
                  {m.entries.map((e) => (
                    <ButtonLink
                      key={`${e.source}:${e.series_id}`}
                      size="sm"
                      variant="secondary"
                      href={routeHref({ name: 'sources', open: { source: e.source, series_id: e.series_id, title: e.title || m.title } })}
                    >
                      Open on {display(e.source)}
                    </ButtonLink>
                  ))}
                </div>
              </li>
            ))}
          </ul>
          {result.results.length > shown && (
            <button type="button" className={buttonClass('secondary')} onClick={() => setShown((c) => c + RESULTS_PAGE)}>
              Show {Math.min(RESULTS_PAGE, result.results.length - shown)} more
            </button>
          )}
        </div>
      )}
    </div>
  )
}
