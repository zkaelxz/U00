/*
 * RecentExtractions (SO16): the pasted-URL imports with no dedicated
 * adapter, newest first, from the shared access-attempt log
 * (GET /api/sources/url/extractions, admin): what worked and how, AI calls,
 * the site profile used, the access facts, and why a page failed. Shown on
 * the Activity tab of Manage sources. Links are scheme+host+path only.
 */
import { ErrorBanner } from '../../components/ErrorBanner'
import { buttonClass } from '../../components/uiClasses'
import type { SourceExtraction } from '../../types/sourcesTools'
import type { Extractions } from './useExtractions'
import { ManageCard } from './ManageCard'
import { extractionAccess, extractionMeta, whenText } from './sourcesToolsFormat'
import './sources-tools.css'

/** The list itself; the Activity tab shows its own empty message when there are no rows. */
export function RecentExtractions({ rows, error, busy, load, clearError }: Extractions & { rows: SourceExtraction[] }) {
  return (
    <ManageCard tab="activity" card="imports" title="Imported from links" count={rows.length}>
      <div className="actions">
        <button type="button" className={buttonClass('ghost', 'sm')} disabled={busy} aria-busy={busy} onClick={load}>
          Refresh
        </button>
      </div>
      <ErrorBanner error={error} onDismiss={clearError} />
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
    </ManageCard>
  )
}
