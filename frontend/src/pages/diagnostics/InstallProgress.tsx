import { useState } from 'react'

import { cancelDependencyInstall } from '../../api/diagnosticsInstalls'
import { buttonClass } from '../../components/uiClasses'
import type { DiagnosticsJobState } from '../../types/diagnosticsInstalls'
import { adminErrorText } from './diagnosticsAdmin'
import { jobProgressLine } from './jobPoll'

/**
 * Progress and Cancel for the install job that is running (it holds the
 * library, so nothing else can run until it ends). Cancel kills pip on the
 * PC; the install then reports as cancelled.
 */
export function InstallProgress({ job, name }: { job: DiagnosticsJobState | null | undefined; name: string }) {
  const [cancelling, setCancelling] = useState(false)
  const [error, setError] = useState<string | null>(null)
  if (!job) return null
  const cancel = () => {
    setCancelling(true)
    setError(null)
    cancelDependencyInstall().catch((e: unknown) => {
      setCancelling(false)
      setError(adminErrorText(e, 'install'))
    })
  }
  return (
    <div className="diag-stack" data-testid="install-progress" role="group" aria-label={`Installing ${name}`}>
      <progress max={1} value={job.progress || 0} aria-label={`Install of ${name} progress`} />
      <p className="muted" aria-live="polite">{jobProgressLine(job)}</p>
      <div className="actions">
        <button type="button" className={buttonClass('secondary', 'sm')} onClick={cancel} disabled={cancelling}
          aria-label={`Cancel installing ${name}`}>
          {cancelling ? 'Cancelling…' : 'Cancel'}
        </button>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
    </div>
  )
}
