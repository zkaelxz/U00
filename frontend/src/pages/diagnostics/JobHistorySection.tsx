import { useState } from 'react'

import { Section } from '../../components/Section'
import type { DiagnosticsJobHistoryItem } from '../../types/diagnostics'
import { HISTORY_PAGE, historySummary } from './diagnosticsAdmin'

/** "Job history": finished jobs in this run of Baihe, newest first. Hidden when empty. */
export function JobHistorySection({ items }: { items: DiagnosticsJobHistoryItem[] | null }) {
  const [all, setAll] = useState(false)
  if (!items || items.length === 0) return null
  const shown = all ? items : items.slice(0, HISTORY_PAGE)
  return (
    <Section title="Job history" count={items.length} storageKey="diagnostics.history">
      <ul className="diag-history" aria-label="Job history">
        {shown.map((h) => (
          <li key={h.job_id}>
            <details>
              <summary>{historySummary(h)}</summary>
              {h.message && <p>{h.message}</p>}
              {h.error && <p className="error">{h.error}</p>}
              {!h.message && !h.error && <p className="muted">No details.</p>}
            </details>
          </li>
        ))}
      </ul>
      {!all && items.length > HISTORY_PAGE && (
        <div className="actions">
          <button type="button" className="link" onClick={() => setAll(true)}>
            Show all {items.length}
          </button>
        </div>
      )}
    </Section>
  )
}
