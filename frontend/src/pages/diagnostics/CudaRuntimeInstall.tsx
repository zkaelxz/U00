import { useId, useState } from 'react'

import { getCudaRuntimeStatus, installCudaRuntime } from '../../api/diagnosticsInstalls'
import { ConfirmButton } from '../../components/ConfirmButton'
import { Section } from '../../components/Section'
import { usePcPendingNote, type PcMode } from '../../hooks/usePcOnly'
import { installBlockedReason, type AdminBusy } from './diagnosticsAdmin'
import { cudaRuntimeErrorText, cudaRuntimeResultLine, CUDA_RUNTIME_CONFIRM, CUDA_RUNTIME_INTRO } from './cudaRuntimeText'
import { jobProgressLine, jobRunning } from './jobPoll'
import { useServerJobStatus } from './useServerJobStatus'

/**
 * Packages and installs: "CUDA runtime for GPU Whisper" (Recommended), shown only
 * when an NVIDIA GPU is present and Whisper's CUDA libraries are found nowhere.
 * PC only; the install is the same server job as a package install.
 */
export function CudaRuntimeInstall({ pc, jobsActive, busy, onStarted, onFinished, openSignal }: {
  pc: PcMode
  jobsActive: boolean
  busy: AdminBusy
  onStarted: () => void
  onFinished: () => void
  openSignal?: number
}) {
  const { status, refresh } = useServerJobStatus(getCudaRuntimeStatus, onFinished)
  const [error, setError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const reasonId = useId()
  const pending = usePcPendingNote(pc)
  if (!status || !status.show) return null

  const job = status.job
  const running = jobRunning(job)
  const blocked = installBlockedReason(jobsActive, busy)
  const result = cudaRuntimeResultLine(status)
  const start = async () => {
    setStarting(true)
    setError(null)
    try {
      await installCudaRuntime()
      onStarted()
    } catch (e) {
      setError(cudaRuntimeErrorText(e))
    } finally {
      setStarting(false)
      await refresh()
    }
  }

  return (
    <Section title="CUDA runtime for GPU Whisper" storageKey="diagnostics.cuda-runtime"
      summary={result?.text ?? 'Recommended'} openSignal={openSignal}>
      <div className="diag-subcard" data-testid="cuda-runtime-install" role="group" aria-label="CUDA runtime for GPU Whisper">
        <p className="muted">{CUDA_RUNTIME_INTRO}</p>
        {running && job && (
          <div className="diag-stack">
            <progress max={1} value={job.progress || 0} aria-label="CUDA runtime install progress" />
            <p className="muted" aria-live="polite">{jobProgressLine(job)}</p>
          </div>
        )}
        {result && (
          <p className={result.tone === 'ok' ? undefined : result.tone} role={result.tone === 'error' ? 'alert' : 'status'}
            data-testid="cuda-runtime-result">
            {result.text}
          </p>
        )}
        {error && <p className="error" role="alert">{error}</p>}
        {pending && status.missing && <p className="muted">{pending}</p>}
        {pc === 'local' && status.missing && !running && (
          <div className="actions">
            <ConfirmButton
              name="CUDA runtime"
              label="Install…"
              ariaLabel="Install CUDA runtime for GPU Whisper"
              verb="install"
              tone="primary"
              confirmLabel={CUDA_RUNTIME_CONFIRM}
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
