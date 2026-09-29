import { useCallback, useEffect, useState } from 'react'

import type { ApiError } from '../api/client'
import { getDiagnostics } from '../api/diagnostics'
import { cancelJob, listJobs } from '../api/jobs'
import { ErrorBanner } from '../components/ErrorBanner'
import { Section } from '../components/Section'
import type { DiagnosticsOverview } from '../types/diagnostics'
import type { JobRecord } from '../types/jobs'
import {
  describeGpu,
  formatDuration,
  hasActiveJobs,
  isActive,
  splitDependencies,
  statusLabel,
} from './diagnosticsFormat'

const POLL_MS = 3000

export default function DiagnosticsPage() {
  const [overview, setOverview] = useState<DiagnosticsOverview | null>(null)
  const [jobs, setJobs] = useState<JobRecord[] | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [now, setNow] = useState(() => Date.now() / 1000)

  const refreshJobs = useCallback(async () => {
    try {
      setJobs((await listJobs()).items)
      setNow(Date.now() / 1000)
    } catch (e) {
      setError(e as ApiError)
    }
  }, [])

  useEffect(() => {
    getDiagnostics().then(setOverview, (e) => setError(e as ApiError))
    listJobs().then(
      (r) => setJobs(r.items),
      (e) => setError(e as ApiError),
    )
  }, [])

  const active = jobs !== null && hasActiveJobs(jobs)
  useEffect(() => {
    if (!active) return
    const t = setInterval(() => void refreshJobs(), POLL_MS)
    return () => clearInterval(t)
  }, [active, refreshJobs])

  const cancel = async (id: string) => {
    try {
      await cancelJob(id)
      setError(null)
    } catch (e) {
      setError(e as ApiError)
    }
    await refreshJobs()
  }

  const deps = overview ? splitDependencies(overview.dependencies) : null
  const missingFiles = overview
    ? overview.file_completeness.missing_top_level.length +
      overview.file_completeness.missing_tabs.length
    : 0

  return (
    <section className="panel" aria-label="Diagnostics">
      <h2>Diagnostics</h2>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      <h3>Optional packages</h3>
      {!deps ? (
        <p className="muted">Loading…</p>
      ) : (
        <div data-testid="dependency-panel">
          <p>
            {deps.installed.length} installed, {deps.missing.length} missing.
          </p>
          {deps.missing.length > 0 && (
            <ul aria-label="Missing packages">
              {deps.missing.map((d) => (
                <li key={d.name}>
                  <strong>{d.name}</strong> is not installed, so this is unavailable: {d.powers}
                </li>
              ))}
            </ul>
          )}
          {deps.installed.length > 0 && (
            <Section
              storageKey="diagnostics.installed"
              title="Installed packages"
              count={deps.installed.length}
            >
              <ul aria-label="Installed packages">
                {deps.installed.map((d) => (
                  <li key={d.name}>
                    <strong>{d.name}</strong> is installed: {d.powers}
                  </li>
                ))}
              </ul>
            </Section>
          )}
        </div>
      )}

      {overview && (
        <Section
          storageKey="diagnostics.system"
          title="System"
          defaultOpen={missingFiles > 0 || !overview.library_writable}
          summary={`${describeGpu(overview.gpu)} · app files ${missingFiles === 0 ? 'all present' : `${missingFiles} missing`}`}
        >
          <ul data-testid="system-summary">
            <li>
              Library folder: {overview.library_writable ? 'can be written to' : 'cannot be written to'}
            </li>
            <li>Graphics card: {describeGpu(overview.gpu)}</li>
            <li>App files: {missingFiles === 0 ? 'all present' : `${missingFiles} missing`}</li>
            {overview.model_engine_versions.map((m) => (
              <li key={m.name}>
                {m.name}: {m.installed ? (m.version ?? 'installed') : 'not installed'}
              </li>
            ))}
          </ul>
        </Section>
      )}

      <h3>Jobs</h3>
      {jobs === null ? (
        <p className="muted">Loading…</p>
      ) : jobs.length === 0 ? (
        <p className="muted">No jobs yet.</p>
      ) : (
        <div className="table-scroll">
          <table data-testid="job-list">
            <thead>
              <tr>
                <th>Job</th>
                <th>Status</th>
                <th>Time</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {jobs.map((j) => (
                <tr key={j.job_id}>
                  <td>{j.description || j.job_id}</td>
                  <td>
                    {statusLabel(j.status)}
                    {j.progress != null && isActive(j.status) && ` ${Math.round(j.progress * 100)}%`}
                    {j.message && <div className="muted">{j.message}</div>}
                  </td>
                  <td>{formatDuration(j, now)}</td>
                  <td>
                    {isActive(j.status) && (
                      <button type="button" onClick={() => void cancel(j.job_id)}>
                        Cancel
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
