import { useState } from 'react'

import { cancelJob } from '../../../api/jobs'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { safeDetail } from '../../../components/errorMessages'
import type { ApiError } from '../../../api/client'
import type { JobRecord } from '../../../types/jobs'
import { TERMINAL_STATUSES } from '../../../types/jobs'

interface Props {
  job: JobRecord | null
  pollError: ApiError | null
}

export function JobPanel({ job, pollError }: Props) {
  const [cancelError, setCancelError] = useState<unknown>(null)
  const active = job !== null && !TERMINAL_STATUSES.includes(job.status)

  return (
    <section className="panel job-panel" aria-label="Job" data-testid="job-panel">
      <h3>Job</h3>
      <ErrorBanner error={pollError} />
      {job ? (
        <>
          <p data-testid="job-status">
            {job.status}
            {job.message ? ` · ${job.message}` : ''}
          </p>
          {job.progress !== null && <progress value={job.progress} max={1} />}
          {job.status === 'error' && job.error && (
            <p className="error" role="alert">
              {safeDetail(job.error) ?? 'The job failed. Details are in the app log.'}
            </p>
          )}
          {active && (
            <button
              type="button"
              onClick={() => cancelJob(job.job_id).then(() => setCancelError(null), setCancelError)}
            >
              Cancel job
            </button>
          )}
          <ErrorBanner error={cancelError} onDismiss={() => setCancelError(null)} />
        </>
      ) : (
        !pollError && <p className="muted">Starting…</p>
      )}
    </section>
  )
}
