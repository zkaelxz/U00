import { useState } from 'react'

import { startSearch } from '../../api/sources'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { Section } from '../../components/Section'
import { Toggle } from '../../components/Toggle'
import { buttonClass } from '../../components/uiClasses'
import { usePersistedState } from '../../hooks/usePersistedState'
import type { OpenSeries, SearchResult, SourceSummary } from '../../types/sources'
import {
  RESULTS_PAGE,
  describeSourceError,
  percent,
  resultsHeader,
  searchDisabledReason,
  searchInSummary,
  searchSourcesParam,
  searchableSources,
  selectedSources,
} from './sourcesFormat'
import type { SourceErrorCopy } from './sourcesFormat'
import { openerKey } from './sourcesFormat'
import type { SourcesJob } from './useSourcesJob'
import { WebSearchFallback } from './WebSearchFallback'

/** One failed source call: the copy, a browser-check link and Try again. */
export function SourceErrorLine({ copy, onRetry }: { copy: SourceErrorCopy; onRetry?: () => void }) {
  return (
    <p className="warn source-error" role="alert">
      {copy.text}
      {copy.openUrl && (
        <>
          {' '}
          <a href={copy.openUrl} target="_blank" rel="noopener noreferrer">
            Open page ↗
          </a>
        </>
      )}
      {copy.retry && onRetry && (
        <>
          {' '}
          <button type="button" className={buttonClass('ghost', 'sm')} onClick={onRetry}>
            Try again
          </button>
        </>
      )}
    </p>
  )
}

type Props = {
  sources: SourceSummary[]
  remote: boolean
  job: SourcesJob<SearchResult>
  // Phone/tablet: an open series replaces the results (kept mounted, so
  // "Show more" and the scroll position survive).
  resultsHidden: boolean
  onOpen: (series: OpenSeries, opener: string) => void
  // Web-search fallback: puts a result's address into the Paste a link box
  // (null where that box is not available).
  onUseLink: ((url: string) => void) | null
}

export function SearchPanel({ sources, remote, job, resultsHidden, onOpen, onUseLink }: Props) {
  const [query, setQuery] = useState('')
  // Unticked source names; everything else searchable is ticked.
  const [excluded, setExcluded] = usePersistedState<string[]>('sources.searchIn', [])
  const [shown, setShown] = useState(RESULTS_PAGE)
  const searchable = searchableSources(sources)
  const names = searchable.map((s) => s.name)
  const selected = selectedSources(names, Array.isArray(excluded) ? excluded : [])
  const running = job.status === 'running'
  const reason = searchDisabledReason(query, names.length, selected.length, remote)
  const display = (name: string) => sources.find((s) => s.name === name)?.display_name ?? name

  function toggleSource(name: string, on: boolean) {
    const next = on ? selected.filter((n) => n !== name).concat(name) : selected.filter((n) => n !== name)
    // Remember only currently searchable names, so stale ones drop out.
    setExcluded(names.filter((n) => !next.includes(n)))
  }

  function submit(e: React.FormEvent) {
    e.preventDefault()
    if (reason || running) return
    setShown(RESULTS_PAGE)
    const q = query.trim()
    const param = searchSourcesParam(names, selected)
    job.start(() => startSearch(q, param))
  }

  const result = job.status === 'done' ? job.result : null
  const errors = result ? Object.entries(result.errors ?? {}) : []

  return (
    <>
      <form role="search" className="sources-search" onSubmit={submit} aria-label="Search sources">
        <div className="sources-search-row">
          <input
            type="search"
            enterKeyHint="search"
            inputMode="search"
            maxLength={200}
            autoComplete="off"
            aria-label="Title"
            placeholder="Title"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <button type="submit" className={buttonClass('primary')} disabled={!!reason || running}>
            Search
          </button>
        </div>
        {reason && <p className="muted sources-reason">{reason}</p>}
        {names.length > 0 && (
          <Section
            title="Search in"
            storageKey="sources.searchIn"
            summary={searchInSummary(selected.length, names.length)}
          >
            <div className="setting-list sources-search-in">
              {searchable.map((s) => (
                <Field key={s.name} label={s.display_name}>
                  <Toggle checked={selected.includes(s.name)} onChange={(on) => toggleSource(s.name, on)} />
                </Field>
              ))}
            </div>
          </Section>
        )}
      </form>

      <ErrorBanner error={job.startError} onDismiss={job.clearStartError} describe={{ serverText: true }} />

      <div aria-live="polite" className="sources-running">
        {running && (
          <p>
            Searching…{percent(job.progress)} ·{' '}
            <button type="button" className={buttonClass('ghost', 'sm')} onClick={job.cancel}>
              Cancel
            </button>
          </p>
        )}
      </div>
      {running && <p className="muted">Each source is paced, so this can take a minute.</p>}

      {job.status === 'error' && job.error && (
        <SourceErrorLine copy={describeSourceError(job.error, 'The search', remote)} />
      )}

      {result && (
        <div className="sources-results" hidden={resultsHidden} data-testid="search-results">
          <div className="list-head">
            <p>
              <strong>{resultsHeader(result.results.length, errors.length)}</strong>
            </p>
            <button type="button" className={buttonClass('ghost', 'sm')} onClick={job.reset}>
              Clear
            </button>
          </div>
          {result.cancelled && <p className="muted">Stopped. Showing what came back before you cancelled.</p>}
          {!result.results.length && <p className="muted">No results for "{result.query}".</p>}
          {!result.results.length && !result.cancelled && (
            <WebSearchFallback key={result.query} query={result.query} onUseLink={onUseLink} />
          )}
          {errors.map(([name, err]) => (
            <SourceErrorLine
              key={name}
              copy={describeSourceError(err, display(name), remote)}
              onRetry={() => job.start(() => startSearch(result.query, searchSourcesParam(names, selected)))}
            />
          ))}
          {result.results.length > 0 && (
            <ul className="source-results">
              {result.results.slice(0, shown).map((m) => (
                <li key={m.key}>
                  <span className="source-result-text">
                    <strong>{m.title}</strong>
                    <span className="muted">on {m.sources.map(display).join(' · ')}</span>
                  </span>
                  <div className="actions">
                    {m.entries.map((e) => (
                      <button
                        key={`${e.source}:${e.series_id}`}
                        type="button"
                        className={buttonClass('secondary', 'sm')}
                        data-opener={openerKey(e.source, e.series_id)}
                        onClick={() =>
                          onOpen({ source: e.source, series_id: e.series_id, title: e.title || m.title },
                            openerKey(e.source, e.series_id))
                        }
                      >
                        Open on {display(e.source)}
                      </button>
                    ))}
                  </div>
                </li>
              ))}
            </ul>
          )}
          {result.results.length > shown && (
            <button type="button" className={buttonClass('secondary')} onClick={() => setShown((n) => n + RESULTS_PAGE)}>
              Show {Math.min(RESULTS_PAGE, result.results.length - shown)} more
            </button>
          )}
        </div>
      )}
    </>
  )
}
