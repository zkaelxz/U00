/*
 * WebSearchFallback (roadmap item 114): under a title search that found
 * nothing on any source, "Search the web" asks the user's own SearXNG
 * server (only when turned on in Settings). Results are links only, marked
 * "Web" so they are never mistaken for source matches: the title opens the
 * page in a new tab, and "Use this link" puts the address into the Paste a
 * link box, whose preview has its own public-address checks. Baihe never
 * opens a result page by itself.
 */
import { useEffect, useState } from 'react'

import { getWebSearchStatus, searchWeb } from '../../api/webSearch'
import { Badge } from '../../components/Badge'
import { buttonClass } from '../../components/uiClasses'
import { routeHref } from '../../router'
import type { WebSearchResults } from '../../types/webSearch'
import { ExternalLink } from '../discover/ExternalLink'
import { webResultsHeader, webSearchErrorMessage } from './webSearchFormat'

type Props = {
  query: string
  // Null: the Paste a link box is not available here (PC only).
  onUseLink: ((url: string) => void) | null
}

export function WebSearchFallback({ query, onUseLink }: Props) {
  const [enabled, setEnabled] = useState<boolean | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [found, setFound] = useState<WebSearchResults | null>(null)

  useEffect(() => {
    let live = true
    getWebSearchStatus().then(
      (s) => live && setEnabled(s.enabled),
      () => live && setEnabled(false),
    )
    return () => {
      live = false
    }
  }, [])

  if (enabled === null) return null
  if (!enabled) {
    return (
      <p className="muted" data-testid="web-search-off">
        Not on any source? A web search through your own SearXNG server can be turned on in{' '}
        <a href={routeHref({ name: 'settings' })}>Settings</a>.
      </p>
    )
  }

  async function run() {
    setBusy(true)
    setError(null)
    try {
      setFound(await searchWeb(query))
    } catch (e) {
      setError(webSearchErrorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="web-search" aria-label="Web search" data-testid="web-search">
      <div className="list-head">
        <p>Not on any source? Try a general web search.</p>
        <button type="button" className={buttonClass('secondary', 'sm')} disabled={busy} aria-busy={busy} onClick={() => void run()}>
          {busy ? 'Searching the web…' : 'Search the web'}
        </button>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {found && (
        <div data-testid="web-results">
          <p>
            <strong>{webResultsHeader(found.results.length)}</strong>{' '}
            <span className="muted">from your SearXNG server, not from Baihe's sources. Check a page before using it.</span>
          </p>
          {found.results.length > 0 && (
            <ul className="source-results" aria-label="Web results">
              {found.results.map((r) => (
                <li key={r.url}>
                  <span className="source-result-text">
                    <span>
                      <Badge tone="neutral">Web</Badge> <ExternalLink href={r.url}>{r.title}</ExternalLink>
                    </span>
                    <span className="muted web-result-domain">{r.domain}</span>
                    {r.snippet && <span className="muted">{r.snippet}</span>}
                  </span>
                  {onUseLink && (
                    <div className="actions">
                      <button type="button" className={buttonClass('secondary', 'sm')} onClick={() => onUseLink(r.url)}>
                        Use this link
                      </button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  )
}
