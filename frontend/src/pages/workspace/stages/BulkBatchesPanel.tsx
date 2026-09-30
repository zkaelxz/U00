import { useCallback, useEffect, useRef, useState } from 'react'

import { cancelBulkTranslation, listBulkTranslations, resumeBulkTranslations } from '../../../api/translateStage'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { humanize } from '../../../components/labels'
import { Section } from '../../../components/Section'
import { Toggle } from '../../../components/Toggle'
import { buttonClass } from '../../../components/uiClasses'
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
  /** Bump to re-read the list (e.g. after a translate run finishes). */
  reloadKey: number
}

export function BulkBatchesPanel({ reloadKey }: Props) {
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
  // Focus for the two-step cancel: "Yes, cancel it" while asking; back to
  // the row's Cancel button (or the note, or the panel) once it closes.
  const yesRef = useRef<HTMLButtonElement>(null)
  const cancelButtons = useRef(new Map<number, HTMLButtonElement>())
  const noteRef = useRef<HTMLParagraphElement>(null)
  const regionRef = useRef<HTMLDivElement>(null)
  const returnFocusTo = useRef<number | null>(null)

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

  useEffect(() => {
    if (confirmId !== null) yesRef.current?.focus()
  }, [confirmId])
  useEffect(() => {
    if (returnFocusTo.current === null || confirmId !== null) return
    const target = cancelButtons.current.get(returnFocusTo.current) ?? noteRef.current ?? regionRef.current
    target?.focus()
    returnFocusTo.current = null
  }, [confirmId, jobs, note])

  if (jobs === null && !error) return null
  // Nothing to show or resume until a batch exists (rule 8); Bulk lives under Advanced.
  if (jobs !== null && jobs.length === 0) return null

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
        // Also re-read: bumping reloads drops any list GET still in flight
        // from before the cancel, so it can't put the old status back.
        refresh()
      }, (e: unknown) => {
        setActionError(e)
        refresh()
      })
      .finally(() => {
        setCancelling(false)
        returnFocusTo.current = id
        setConfirmId(null)
      })
  }

  const action = (j: BulkJobEntry) => {
    if (!j.cancellable) return null
    if (confirmId !== j.bulk_job_id) {
      return (
        <button type="button" className={buttonClass('danger', 'sm')} aria-label={`Cancel batch ${j.bulk_job_id}`}
          ref={(el) => {
            if (el) cancelButtons.current.set(j.bulk_job_id, el)
            else cancelButtons.current.delete(j.bulk_job_id)
          }}
          disabled={cancelling} onClick={() => { setNote(null); setActionError(null); setConfirmId(j.bulk_job_id) }}>
          Cancel batch
        </button>
      )
    }
    return (
      <span className="bulk-confirm">
        <span role="alert">Cancel batch #{j.bulk_job_id}? Results that arrive later are ignored.</span>
        <button type="button" className={buttonClass('danger', 'sm')} ref={yesRef} disabled={cancelling} onClick={() => cancel(j.bulk_job_id)}>
          {cancelling ? 'Cancelling…' : 'Yes, cancel it'}
        </button>
        <button type="button" className={buttonClass('secondary', 'sm')} disabled={cancelling} onClick={() => { returnFocusTo.current = j.bulk_job_id; setConfirmId(null) }}>Keep it</button>
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
  const engineText = (j: BulkJobEntry) => `${humanize('engine', j.engine)}${j.model ? ` (${j.model})` : ''}`

  return (
    <Section
      storageKey="translate.bulk"
      title="Bulk batches"
      count={jobs ? pending.length : undefined}
      defaultOpen={hasPending}
      summary={jobs ? (hasPending ? `${pending.length} pending` : 'none pending') : undefined}
    >
      <div role="region" aria-label="Bulk batches" className="bulk-batches" ref={regionRef} tabIndex={-1}>
        <div className="bulk-actions">
          <button type="button" className={buttonClass('secondary', 'sm')} disabled={resuming} onClick={resume}>
            {resuming ? 'Resuming…' : 'Resume pending batches'}
          </button>
          <button type="button" className={buttonClass('ghost', 'sm')} onClick={refresh}>Refresh</button>
        </div>
        <p className="muted">Resume after a restart to check each pending batch with its provider again.</p>
        {finished.length > 0 && (
          <div className="setting-list">
            <Field label={`Show finished (${finished.length})`}>
              <Toggle checked={showFinished} onChange={setShowFinished} />
            </Field>
          </div>
        )}
        {resumed && <p className="muted" data-testid="bulk-resume">{resumed}</p>}
        {note && <p className="muted" role="status" ref={noteRef} tabIndex={-1}>{note}</p>}
        <ErrorBanner error={error} onDismiss={() => setError(null)} />
        <ErrorBanner error={actionError} onDismiss={() => setActionError(null)} />
        {jobs && shown.length === 0 && (
          <p className="muted">
            No pending batches.
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
