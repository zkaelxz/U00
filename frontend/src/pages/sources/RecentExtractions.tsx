/*
 * RecentExtractions (SO16): the pasted-URL imports with no dedicated
 * adapter, newest first, from the shared access-attempt log
 * (GET /api/sources/url/extractions, admin): what worked and how, AI calls,
 * the site profile used, the access facts, and why a page failed. Shown in
 * Source settings next to Site profiles (whose "Make active" is the
 * rollback). Links are scheme+host+path only.
 */
import { useCallback, useEffect, useState } from 'react'

import { getPcMode, loadPcMode } from '../../api/pcOnly'
import { listExtractions } from '../../api/sourcesTools'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Section } from '../../components/Section'
import { buttonClass } from '../../components/uiClasses'
import type { SourceExtraction } from '../../types/sourcesTools'
import { extractionAccess, extractionMeta, whenText } from './sourcesToolsFormat'
import './sources-tools.css'

export function RecentExtractions() {
  const [rows, setRows] = useState<SourceExtraction[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    setBusy(true)
    setError(null)
    listExtractions(15)
      .then(setRows, setError)
      .finally(() => setBusy(false))
  }, [])

  // Like the rest of Source settings: wait for /api/meta, so a remote
  // viewer never sends this admin request.
  useEffect(() => {
    let off = false
    void loadPcMode().then(() => {
      if (!off && getPcMode() !== 'remote') load()
    })
    return () => {
      off = true
    }
  }, [load])

  return (
    <Section
      title="Pasted-link imports"
      count={rows?.length ?? undefined}
      summary={rows && rows.length === 0 ? 'none yet' : undefined}
      storageKey="sources.extractions"
    >
      <div className="actions">
        <button type="button" className={buttonClass('ghost', 'sm')} disabled={busy} aria-busy={busy} onClick={load}>
          Refresh
        </button>
      </div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {rows && rows.length === 0 && <p className="muted">No pasted-link imports yet.</p>}
      {rows && rows.length > 0 && (
        <ol className="sources-extractions" data-testid="recent-extractions">
          {rows.map((a, i) => (
            <li key={`${a.created_at ?? ''}-${i}`}>
              <span className="sources-link">
                <strong>{a.url || '(no link)'}</strong>
                {a.created_at ? <span className="muted"> · {whenText(a.created_at)}</span> : null}
              </span>
              <span>{a.headline}</span>
              <span className="muted">{extractionMeta(a)}</span>
              {a.profile && <span className="muted">{a.profile}</span>}
              {a.reason && <span className="muted">Why: {a.reason}</span>}
              {(a.lines.length > 0 || a.access) && (
                <details>
                  <summary className="muted">Details</summary>
                  <ul className="muted">
                    {extractionAccess(a).map((l, j) => (
                      <li key={`a${j}`}>{l}</li>
                    ))}
                    {a.lines.map((l, j) => (
                      <li key={`l${j}`}>{l}</li>
                    ))}
                  </ul>
                </details>
              )}
            </li>
          ))}
        </ol>
      )}
    </Section>
  )
}
