import { useCallback, useState } from 'react'

import { startModelDownload } from '../../api/diagnostics'
import { cancelJob, probeJob } from '../../api/jobs'
import { describeError } from '../../components/errorMessages'
import { buttonClass } from '../../components/uiClasses'
import type { DiagnosticsModelDownload } from '../../types/diagnostics'
import { modelDownloadProblem } from './diagnosticsAdmin'
import { jobProgressLine, jobRunning } from './jobPoll'
import { useServerJobStatus } from './useServerJobStatus'

/**
 * "Download" for a model that is not on disk yet (PC only; the server refuses other
 * devices). The server runs it as a job: this shows its progress with Cancel, or why
 * the last try stopped, and tells the Models card when it ends so the row refreshes.
 */
export function ModelDownload({ model, onFinished }: {
  model: DiagnosticsModelDownload
  onFinished: () => void
}) {
  const load = useCallback(async () => ({ job: await probeJob(model.job_id) }), [model.job_id])
  const { status, running, refresh } = useServerJobStatus(load, onFinished)
  const [error, setError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)

  const start = async () => {
    setStarting(true)
    setError(null)
    try {
      await startModelDownload(model.id)
    } catch (e) {
      setError(describeError(e, { pcOnly: true, serverText: true }).title)
    } finally {
      setStarting(false)
      await refresh()
    }
  }
  const stop = () => cancelJob(model.job_id).then(refresh, (e: unknown) => setError(describeError(e).title))

  const job = status?.job
  const problem = running ? null : modelDownloadProblem(job, model.label)
  return (
    <span className="model-download" data-testid={`model-download-${model.id}`}>
      {running && job ? (
        <>
          <progress max={1} value={job.progress || 0} aria-label={`${model.label} download progress`} />
          <span className="muted" aria-live="polite">{jobProgressLine({ progress: job.progress ?? 0, message: job.message })}</span>
          <button type="button" className={buttonClass('secondary')} onClick={() => void stop()}>Cancel</button>
        </>
      ) : (
        <button type="button" className={buttonClass('secondary')} disabled={starting || jobRunning(job)}
          aria-label={`Download ${model.label}`} onClick={() => void start()}>
          Download
        </button>
      )}
      {problem && <span className="error" role="alert"> {problem}</span>}
      {error && <span className="error" role="alert"> {error}</span>}
    </span>
  )
}
