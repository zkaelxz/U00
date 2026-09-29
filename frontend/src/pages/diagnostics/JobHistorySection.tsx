import { useState } from 'react'

import { Section } from '../../components/Section'
import { buttonClass } from '../../components/uiClasses'
import type { DiagnosticsJobHistoryItem } from '../../types/diagnostics'
import { jobDetail } from '../diagnosticsFormat'
import { HISTORY_PAGE, historySummary } from './diagnosticsAdmin'

/** "Job history": finished jobs in this run of Baihe, newest first. Hidden when empty. */
export function JobHistorySection({ items }: { items: DiagnosticsJobHistoryItem[] | null }) {
  const [all, setAll] = useState(false)
  if (!items || items.length === 0) return null
  const shown = all ? items : items.slice(0, HISTORY_PAGE)
  return (
    <Section title="Job history" count={items.length} storageKey="diagnostics.history">
      <ul className="diag-history" aria-label="Job history">
        {shown.map((h) => {
          // A done or cancelled job's last progress text ("Transcribing...
          // 99%") would read as stuck, so it is hidden; a failure shows its
          // error, a queued one its waiting note (same rule as the live Jobs list).
          const detail = jobDetail({ status: h.status ?? 'done', message: h.message, error: h.error })
          return (
            <li key={h.job_id}>
              <details>
                <summary>{historySummary(h)}</summary>
                {detail ? (
                  <p className={h.status === 'error' ? 'error' : undefined}>{detail}</p>
                ) : (
                  <p className="muted">No details.</p>
                )}
              </details>
            </li>
          )
        })}
      </ul>
      {!all && items.length > HISTORY_PAGE && (
        <div className="actions">
          <button type="button" className={buttonClass('ghost', 'sm')} onClick={() => setAll(true)}>
            Show all {items.length}
          </button>
        </div>
      )}
    </Section>
  )
}
