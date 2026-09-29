/*
 * Discover > Search baihehub (DI06): baihehub's own public search (fixed
 * host, no AI). An English query can be translated to Chinese first. When
 * nothing comes back, a link runs the same search in the browser.
 */
import { useState, type FormEvent } from 'react'

import { baihehubSearch, translateQuery } from '../../api/discover'
import { ExternalLink } from './ExternalLink'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import type { BaihehubResult } from '../../types/discover'
import { hasChinese } from './discoverFormat'

export function BaihehubPanel({ engine, canTranslate }: { engine: string; canTranslate: boolean }) {
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [searched, setSearched] = useState<string | null>(null)
  const [result, setResult] = useState<BaihehubResult | null>(null)

  async function search(e: FormEvent) {
    e.preventDefault()
    const text = q.trim()
    if (!text || busy) return
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      let zh = text
      if (canTranslate && !hasChinese(text)) {
        zh = await translateQuery(text, engine || undefined).then((r) => r.translated || text, () => text)
      }
      setSearched(zh)
      setResult(await baihehubSearch(zh))
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="discover-block" onSubmit={search}>
      <p className="muted discover-lead">
        Searches baihehub's books, audio dramas and manhua through its public search.
        {canTranslate ? ' English is translated to Chinese first.' : ''} Needs an internet connection.
      </p>
      <div className="discover-row">
        <Field label="Title to search">
          <input type="search" value={q} maxLength={200} onChange={(e) => setQ(e.target.value)} />
        </Field>
        <button type="submit" disabled={!q.trim() || busy} aria-busy={busy}>
          {busy ? 'Searching…' : 'Search'}
        </button>
      </div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {searched && result && (
        <div className="discover-result" data-testid="baihehub-result">
          <p className="muted">Searched for: {searched}</p>
          {result.results.length > 0 ? (
            <ul className="discover-links">
              {result.results.map((h) => (
                <li key={h.url}>
                  <ExternalLink href={h.url}>
                    {h.title}
                  </ExternalLink>
                  {h.snippet && <span className="muted"> — {h.snippet}</span>}
                </li>
              ))}
            </ul>
          ) : (
            <p className="warn">
              Nothing came back.{' '}
              <ExternalLink href={result.fallback_url}>
                Run this search in your browser
              </ExternalLink>{' '}
              and add anything you find with “Add a title”.
            </p>
          )}
        </div>
      )}
    </form>
  )
}
