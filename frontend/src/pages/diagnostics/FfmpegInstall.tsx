import { useId, useState } from 'react'

import { getFfmpegInstallStatus, installFfmpeg } from '../../api/ffmpegInstall'
import { cancelJob } from '../../api/jobs'
import { ConfirmButton } from '../../components/ConfirmButton'
import { Section } from '../../components/Section'
import { buttonClass } from '../../components/uiClasses'
import { usePcPendingNote, type PcMode } from '../../hooks/usePcOnly'
import { adminErrorText, installBlockedReason, type AdminBusy } from './diagnosticsAdmin'
import { FFMPEG_INTRO, canOfferFfmpeg, ffmpegConfirm, ffmpegResultLine, ffmpegStatusLine, showFfmpeg } from './ffmpegInstallText'
import { jobProgressLine } from './jobPoll'
import { useServerJobStatus } from './useServerJobStatus'

/**
 * Packages and installs card: "Install ffmpeg" when none is found (PC only, two-step). The server
 * runs the download as a job; this polls its status, offers Cancel while it runs, then shows the result.
 */
export function FfmpegInstall({ pc, jobsActive, busy, onStarted, onFinished, openSignal }: {
  pc: PcMode
  jobsActive: boolean
  busy: AdminBusy
  onStarted: () => void
  onFinished: () => void
  openSignal?: number
}) {
  const { status, running, refresh } = useServerJobStatus(getFfmpegInstallStatus, onFinished)
  const [error, setError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const reasonId = useId()
  const pending = usePcPendingNote(pc)
  if (!status || !showFfmpeg(status)) return null

  const local = pc === 'local'
  const blocked = installBlockedReason(jobsActive, busy)
  const line = ffmpegStatusLine(status)
  const result = ffmpegResultLine(status)
  const start = async () => {
    setStarting(true)
    setError(null)
    try {
      await installFfmpeg()
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
    <Section title="ffmpeg" storageKey="diagnostics.ffmpeg" summary={line ?? 'Not found'} openSignal={openSignal}>
    <div className="diag-subcard ffmpeg-install" data-testid="ffmpeg-install" role="group" aria-label="ffmpeg">
      <p className="muted">{FFMPEG_INTRO}</p>
      {line && <p className="muted" data-testid="ffmpeg-status">{line}</p>}
      {running && status.job && (
        <div className="diag-stack" data-testid="ffmpeg-progress">
          <progress max={1} value={status.job.progress || 0} aria-label="ffmpeg install progress" />
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
          data-testid="ffmpeg-result">
          {result.text}
        </p>
      )}
      {error && <p className="error" role="alert">{error}</p>}
      {pending && canOfferFfmpeg(status) && <p className="muted">{pending}</p>}
      {local && canOfferFfmpeg(status) && (
        <div className="actions">
          <ConfirmButton
            name="ffmpeg"
            label="Install ffmpeg…"
            ariaLabel="Install ffmpeg"
            verb="install"
            tone="primary"
            confirmLabel={ffmpegConfirm(status)}
            disabled={!!blocked}
            describedBy={blocked ? reasonId : undefined}
            busy={starting}
            onConfirm={() => void start()}
          />
          {blocked && <span className="muted" id={reasonId}>{blocked}</span>}
        </div>
      )}
    </div>
    </Section>
  )
}
