import { artifactDownloadUrl } from '../../api/libraryAdmin'
import { ErrorBanner } from '../../components/ErrorBanner'
import { safeDetail } from '../../components/errorMessages'
import { jobFailed } from '../../types/jobs'
import type { ArtifactKind } from '../../types/libraryAdmin'
import { formatBytes, percent } from './libraryAdmin'
import type { AdminJob } from './useAdminJob'

/** "Working… 40% · Cancel" while running; the failure or download link after. */
export function AdminJobLine({ job, busyText, artifact, showLink = true }: {
  job: AdminJob
  busyText: string
  artifact?: ArtifactKind
  showLink?: boolean
}) {
  const j = job.job
  return (
    <div className="admin-job" aria-live="polite">
      {job.active && (
        <p className="actions">
          <span data-testid="admin-job-status">
            {j?.status === 'queued' ? 'Waiting for another job…' : busyText}
            {j && percent(j.progress) && ` ${percent(j.progress)}`}
          </span>
          {j && (
            <button type="button" className="link" onClick={job.cancel}>
              Cancel
            </button>
          )}
        </p>
      )}
      {j && job.done && jobFailed(j) && (
        <p className="error" role="alert">
          {j.status === 'cancelled'
            ? 'Cancelled.'
            : (j.error && safeDetail(j.error)) || 'The job failed. Details are in the app log.'}
        </p>
      )}
      <ErrorBanner error={job.pollError} />
      {artifact && showLink && job.info && !job.active && (
        <p>
          <a href={artifactDownloadUrl(artifact)} download={job.info.name} data-testid={`download-${artifact}`}>
            Download {job.info.name}
          </a>{' '}
          <span className="muted">({formatBytes(job.info.size)})</span>
        </p>
      )}
    </div>
  )
}
