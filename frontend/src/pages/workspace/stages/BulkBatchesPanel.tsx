import { useCallback, useEffect, useState } from 'react'

import { cancelBulkTranslation, listBulkTranslations, resumeBulkTranslations } from '../../../api/translateStage'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Section } from '../../../components/Section'
import { useMediaQuery } from '../../../hooks/useMediaQuery'
import type { BulkJobEntry } from '../../../types/translateStage'
import { useStage } from '../StageContext'
import {
  kindLabel,
  plainServerText,
  replaceJob,
  resumeText,
  shortTime,
  splitJobs,
  statusLabel,
  statusTone,
  summaryText,
} from './bulkBatches'

// Re-reads the list while something is pending. The list is a database
// read (never contacts a provider); the server's own poller moves it on.
const REFRESH_MS = 30_000

type Props = {
  /** Offer the panel even with no batches yet (a bulk-capable engine exists). */
  supported: boolean
  /** Bump to re-read the list (e.g. after a translate run finishes). */
  reloadKey: number
}

export function BulkBatchesPanel({ supported, reloadKey }: Props) {
  const { dramaId } = useStage()
  const phone = useMediaQuery('(max-width: 640px)')
  const [jobs, setJobs] = useState<BulkJobEntry[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  // Cancel/resume failures: kept apart so the list re-read after a 409 does not clear them.
  const [actionError, setActionError] = useState<unknown>(null)
  const [reloads, setReloads] = useState(0)
  const [showFinished, setShowFinished] = useState(false)
  const [confirmId, setConfirmId] = useState<number | null>(null)
  const [cancelling, setCancelling] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [resumed, setResumed] = useState<string | null>(null)
  const [resuming, setResuming] = useState(false)
  const refresh = useCallback(() => setReloads((n) => n + 1), [])

  useEffect(() => {
    let cancelled = false
    listBulkTranslations(dramaId).then(
      (r) => {
        if (cancelled) return
        setJobs(r.jobs)
        setError(null)
      },
      (e: unknown) => !cancelled && setError(e),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId, reloads, reloadKey])

  const { pending, finished } = splitJobs(jobs ?? [])
  const hasPending = pending.length > 0
  useEffect(() => {
    if (!hasPending) return
    const t = window.setInterval(refresh, REFRESH_MS)
    return () => window.clearInterval(t)
  }, [hasPending, refresh])

  if (jobs === null && !error) return null
  if (!supported && jobs !== null && jobs.length === 0) return null

  const resume = () => {
    setResuming(true)
    resumeBulkTranslations(dramaId)
      .then((r) => {
        setActionError(null)
        setResumed(resumeText(r.jobs))
        refresh()
      }, setActionError)
      .finally(() => setResuming(false))
  }

  const cancel = (id: number) => {
    setCancelling(true)
    cancelBulkTranslation(dramaId, id)
      .then((r) => {
        setActionError(null)
        setJobs((cur) => (cur ? replaceJob(cur, r.bulk_job) : cur))
        setNote(`#${id}: ${plainServerText(r.message, 'Cancelled.')}`)
      }, (e: unknown) => {
        setActionError(e)
        refresh()
      })
      .finally(() => {
        setCancelling(false)
        setConfirmId(null)
      })
  }

  const action = (j: BulkJobEntry) => {
    if (!j.cancellable) return null
    if (confirmId !== j.bulk_job_id) {
      return (
        <button type="button" className="danger" aria-label={`Cancel batch ${j.bulk_job_id}`}
          disabled={cancelling} onClick={() => { setNote(null); setActionError(null); setConfirmId(j.bulk_job_id) }}>
          Cancel batch
        </button>
      )
    }
    return (
      <span className="bulk-confirm">
        <span role="alert">Cancel batch #{j.bulk_job_id}? Results that arrive later are ignored.</span>
        <button type="button" className="danger" disabled={cancelling} onClick={() => cancel(j.bulk_job_id)}>
          {cancelling ? 'Cancelling…' : 'Yes, cancel it'}
        </button>
        <button type="button" disabled={cancelling} onClick={() => setConfirmId(null)}>Keep it</button>
      </span>
    )
  }

  const detail = (j: BulkJobEntry) => {
    const bits: string[] = []
    if (j.status === 'scheduled' && j.scheduled_for) bits.push(`runs ${shortTime(j.scheduled_for)}`)
    const s = summaryText(j.result_summary)
    if (s) bits.push(s)
    return bits.join(' · ')
  }
  const errorLine = (j: BulkJobEntry) =>
    j.last_error ? <span className="error bulk-error">{plainServerText(j.last_error, 'The provider reported a problem.')}</span> : null

  const shown = showFinished ? [...pending, ...finished] : pending
  const engineText = (j: BulkJobEntry) => `${j.engine}${j.model ? ` (${j.model})` : ''}`

  return (
    <Section
      storageKey="translate.bulk"
      title="Bulk batches"
      count={jobs ? pending.length : undefined}
      defaultOpen={hasPending}
      summary={jobs ? (hasPending ? `${pending.length} pending` : 'none pending') : undefined}
    >
      <div role="region" aria-label="Bulk batches" className="bulk-batches">
        <div className="bulk-actions">
          <button type="button" disabled={resuming} onClick={resume}
            title="After a restart, starts checking each pending batch with its provider again.">
            {resuming ? 'Resuming…' : 'Resume pending batches'}
          </button>
          <button type="button" onClick={refresh}>Refresh</button>
          {finished.length > 0 && (
            <label className="inline">
              <input type="checkbox" checked={showFinished} onChange={(e) => setShowFinished(e.target.checked)} />{' '}
              Show finished ({finished.length})
            </label>
          )}
        </div>
        {resumed && <p className="muted" data-testid="bulk-resume">{resumed}</p>}
        {note && <p className="muted" role="status">{note}</p>}
        <ErrorBanner error={error} onDismiss={() => setError(null)} />
        <ErrorBanner error={actionError} onDismiss={() => setActionError(null)} />
        {jobs && shown.length === 0 && (
          <p className="muted">
            {jobs.length === 0
              ? 'No bulk batches yet. Tick Bulk under Advanced to send the drama as one discounted batch.'
              : 'No pending batches.'}
          </p>
        )}
        {shown.length > 0 && phone && (
          <ul className="bulk-list">
            {shown.map((j) => (
              <li key={j.bulk_job_id} data-testid="bulk-batch">
                <div><strong>#{j.bulk_job_id} {kindLabel(j)}</strong> · {j.line_count} line{j.line_count === 1 ? '' : 's'}</div>
                <div className="muted">{engineText(j)} · updated {shortTime(j.updated_at || j.submitted_at) || 'unknown'}</div>
                <div><span className={`badge ${statusTone(j.status)}`}>{statusLabel(j.status)}</span> <span className="muted">{detail(j)}</span></div>
                {errorLine(j)}
                {action(j)}
              </li>
            ))}
          </ul>
        )}
        {shown.length > 0 && !phone && (
          <div className="table-scroll"><table className="bulk-table">
            <thead>
              <tr><th>Batch</th><th>What</th><th>Engine</th><th>Lines</th><th>Status</th><th>Updated</th><th /></tr>
            </thead>
            <tbody>
              {shown.map((j) => (
                <tr key={j.bulk_job_id} data-testid="bulk-batch">
                  <td>#{j.bulk_job_id}</td>
                  <td>{kindLabel(j)}</td>
                  <td>{engineText(j)}</td>
                  <td>{j.line_count}</td>
                  <td>
                    <span className={`badge ${statusTone(j.status)}`}>{statusLabel(j.status)}</span>
                    {detail(j) && <div className="muted">{detail(j)}</div>}
                    {errorLine(j)}
                  </td>
                  <td>{shortTime(j.updated_at || j.submitted_at)}</td>
                  <td>{action(j)}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
      </div>
    </Section>
  )
}
