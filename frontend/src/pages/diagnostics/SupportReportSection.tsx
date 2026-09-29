import { useState } from 'react'

import { getSupportReport } from '../../api/diagnostics'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Section } from '../../components/Section'
import { CopyBlock } from './LogSection'

/** "Support report": a redacted plain-text summary to paste into a bug report. */
export function SupportReportSection() {
  const [report, setReport] = useState<string | null>(null)
  const [building, setBuilding] = useState(false)
  const [error, setError] = useState<unknown>(null)

  const build = () => {
    setBuilding(true)
    setError(null)
    getSupportReport().then(
      (r) => {
        setReport(r.report)
        setBuilding(false)
      },
      (e: unknown) => {
        setError(e)
        setBuilding(false)
      },
    )
  }

  return (
    <Section title="Support report" storageKey="diagnostics.report" summary="Redacted; safe to share">
      <div className="diag-stack">
        <div className="actions">
          <button type="button" disabled={building} onClick={build}>
            {building ? 'Building…' : report === null ? 'Build report' : 'Build again'}
          </button>
        </div>
        <ErrorBanner error={error} onDismiss={() => setError(null)} />
        {report !== null && <CopyBlock text={report} label="Support report" />}
      </div>
    </Section>
  )
}
