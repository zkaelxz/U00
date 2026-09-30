import { useId, useState } from 'react'

import { getDenoStatus, installDeno } from '../../api/diagnosticsInstalls'
import { ConfirmButton } from '../../components/ConfirmButton'
import { usePcPendingNote, type PcMode } from '../../hooks/usePcOnly'
import { DENO_CONFIRM, canOfferDeno, denoIntro, denoNote, denoResultLine, showDeno } from './denoInstallText'
import { adminErrorText, installBlockedReason, type AdminBusy } from './diagnosticsAdmin'
import { jobProgressLine } from './jobPoll'
import { useServerJobStatus } from './useServerJobStatus'

/**
 * Setup card, under the rows: "Install Deno…" when no JS runtime is found
 * (PC only, two-step). The server runs it as a job; this polls its status
 * and shows progress, then the result. Other admin buttons wait for it
 * (it is a running job), and it waits for them.
 */
export function DenoInstall({ pc, jobsActive, busy, onStarted, onFinished }: {
  pc: PcMode
  jobsActive: boolean
  busy: AdminBusy
  // A job started (refresh the jobs list) / finished (re-run the setup checks).
  onStarted: () => void
  onFinished: () => void
}) {
  const { status, running, refresh } = useServerJobStatus(getDenoStatus, onFinished)
  const [error, setError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const reasonId = useId()
  const pending = usePcPendingNote(pc)
  if (!status || !showDeno(status)) return null

  const local = pc === 'local'
  const blocked = installBlockedReason(jobsActive, busy)
  const note = denoNote(status)
  const result = denoResultLine(status)
  const start = async () => {
    setStarting(true)
    setError(null)
    try {
      await installDeno()
      onStarted()
    } catch (e) {
      setError(adminErrorText(e, 'install'))
    } finally {
      setStarting(false)
      await refresh()
    }
  }

  return (
    <div className="diag-subcard deno-install" data-testid="deno-install" role="group" aria-label="JavaScript runtime">
      <p className="muted">{denoIntro(status.install_method)}</p>
      {running && status.job && (
        <div className="diag-stack" data-testid="deno-progress">
          <progress max={1} value={status.job.progress || 0} aria-label="Deno install progress" />
          <p className="muted" aria-live="polite">{jobProgressLine(status.job)}</p>
        </div>
      )}
      {result && (
        <p className={result.tone === 'ok' ? undefined : result.tone} role={result.tone === 'error' ? 'alert' : 'status'}
          data-testid="deno-result">
          {result.text}
        </p>
      )}
      {note && <p className="muted" data-testid="deno-note">{note}</p>}
      {error && <p className="error" role="alert">{error}</p>}
      {pc === 'remote' && canOfferDeno(status) && <p className="muted">Installing is PC only.</p>}
      {pending && canOfferDeno(status) && <p className="muted">{pending}</p>}
      {local && canOfferDeno(status) && (
        <div className="actions">
          <ConfirmButton
            name="Deno"
            label="Install Deno…"
            ariaLabel="Install Deno"
            verb="install"
            tone="primary"
            confirmLabel={DENO_CONFIRM}
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
