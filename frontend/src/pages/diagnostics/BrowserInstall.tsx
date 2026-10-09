import { useId, useState } from 'react'

import { getBrowserInstallStatus, installBrowser } from '../../api/browserInstall'
import { cancelJob } from '../../api/jobs'
import { ConfirmButton } from '../../components/ConfirmButton'
import { buttonClass } from '../../components/uiClasses'
import { usePcPendingNote, type PcMode } from '../../hooks/usePcOnly'
import { BROWSER_CONFIRM, BROWSER_INTRO, browserResultLine, browserStatusLine, canOfferBrowser, showBrowser } from './browserInstallText'
import { adminErrorText, installBlockedReason, type AdminBusy } from './diagnosticsAdmin'
import { jobProgressLine } from './jobPoll'
import { useServerJobStatus } from './useServerJobStatus'

/**
 * Setup card: "Install browser support" when no Chrome or Edge is found
 * (PC only, two-step). The server runs the download as a job; this polls
 * its status, offers Cancel while it runs, then shows the result.
 */
export function BrowserInstall({ pc, jobsActive, busy, onStarted, onFinished }: {
  pc: PcMode
  jobsActive: boolean
  busy: AdminBusy
  onStarted: () => void
  onFinished: () => void
}) {
  const { status, running, refresh } = useServerJobStatus(getBrowserInstallStatus, onFinished)
  const [error, setError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const reasonId = useId()
  const pending = usePcPendingNote(pc)
  if (!status || !showBrowser(status)) return null

  const local = pc === 'local'
  const blocked = installBlockedReason(jobsActive, busy)
  const line = browserStatusLine(status)
  const result = browserResultLine(status)
  const tail = status.last_result?.output_tail ?? []
  const start = async () => {
    setStarting(true)
    setError(null)
    try {
      await installBrowser()
      onStarted()
    } catch (e) {
      setError(adminErrorText(e, 'install'))
    } finally {
      setStarting(false)
      await refresh()
    }
  }
  const cancel = async () => {
    setError(null)
    try {
      await cancelJob(status.job_id)
    } catch (e) {
      setError(adminErrorText(e, 'install'))
    } finally {
      await refresh()
    }
  }

  return (
    <div className="diag-subcard browser-install" data-testid="browser-install" role="group" aria-label="Browser support">
      <p><strong>Install browser support</strong></p>
      <p className="muted">{BROWSER_INTRO}</p>
      {line && <p className="muted" data-testid="browser-status">{line}</p>}
      {running && status.job && (
        <div className="diag-stack" data-testid="browser-progress">
          <progress max={1} value={status.job.progress || 0} aria-label="Browser install progress" />
          <p className="muted" aria-live="polite">{jobProgressLine(status.job)}</p>
          {local && (
            <div className="actions">
              <button type="button" className={buttonClass('secondary')} onClick={() => void cancel()}>
                Cancel install
              </button>
            </div>
          )}
        </div>
      )}
      {result && (
        <p className={result.tone === 'ok' ? undefined : 'error'} role={result.tone === 'error' ? 'alert' : 'status'}
          data-testid="browser-result">
          {result.text}
        </p>
      )}
      {result?.tone === 'error' && tail.length > 0 && (
        <details>
          <summary>Installer output</summary>
          <pre className="diag-pre">{tail.join('\n')}</pre>
        </details>
      )}
      {error && <p className="error" role="alert">{error}</p>}
      {pc === 'remote' && canOfferBrowser(status) && <p className="muted">Installing is PC only.</p>}
      {pending && canOfferBrowser(status) && <p className="muted">{pending}</p>}
      {local && canOfferBrowser(status) && (
        <div className="actions">
          <ConfirmButton
            name="browser support"
            label="Install browser support…"
            ariaLabel="Install browser support"
            verb="install"
            tone="primary"
            confirmLabel={BROWSER_CONFIRM}
            disabled={!!blocked}
            describedBy={blocked ? reasonId : undefined}
            busy={starting}
            onConfirm={() => void start()}
          />
          {blocked && <span className="muted" id={reasonId}>{blocked}</span>}
        </div>
      )}
    </div>
  )
}
