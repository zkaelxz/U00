import { useEffect, useRef, useState } from 'react'

import { cancelJob } from '../../../../api/jobs'
import { applyRetime, getRetimeResult, startRetime } from '../../../../api/workspace'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { Section } from '../../../../components/Section'
import { useJob, useJobRun } from '../../../../hooks/useJob'
import { TERMINAL_STATUSES } from '../../../../types/jobs'
import type { RetimeProposal, RetimeResult } from '../../../../types/workspace'
import { useLineSelectionContext } from './LineSelectionContext'
import { formatTime } from './reviewLogic'
import { retimeOutcome, retimeSelectedProblem, startShift } from './retimeLogic'

// Lets the Qwen3 forced aligner propose new start/end times for the ticked
// lines, keeping their words. Nothing is written until "Use this" / "Use all
// shown"; the server then writes only the times, for lines unchanged since
// the run, after a history snapshot (Review's history can undo it).
export function RetimeLines({
  dramaId,
  jobRunning,
  onChanged,
  openSignal,
}: {
  dramaId: number
  jobRunning: boolean
  onChanged: () => void
  // Each change (the selection bar's action) opens the section and scrolls to it.
  openSignal?: number
}) {
  const { selectedIds } = useLineSelectionContext()
  const panelRef = useRef<HTMLDivElement>(null)
  const [sectionSignal, setSectionSignal] = useState(0)
  const [error, setError] = useState<unknown>(null)
  const [starting, setStarting] = useState(false)
  const [applying, setApplying] = useState(false)
  const [jobId, setJobId, runKey] = useJobRun()
  const [result, setResult] = useState<RetimeResult | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  useEffect(() => {
    if (!openSignal) return
    setSectionSignal((n) => n + 1)
    panelRef.current?.scrollIntoView({ block: 'start' })
  }, [openSignal])

  const { job, error: pollError } = useJob(jobId, {
    runKey,
    onDone: (j) => {
      const o = retimeOutcome(j)
      if (o.kind === 'none') {
        setFailure(o.text)
        return
      }
      getRetimeResult(dramaId).then(setResult, setError)
    },
  })

  const active = !!job && !TERMINAL_STATUSES.includes(job.status)
  const busy = starting || active || (!!jobId && !job && !pollError)
  const blocked = retimeSelectedProblem(selectedIds.length) ?? (jobRunning && !busy ? 'Another job is running on this title. Try again when it finishes.' : null)

  const start = () => {
    setError(null)
    setResult(null)
    setFailure(null)
    setNote(null)
    setStarting(true)
    startRetime(dramaId, { line_ids: selectedIds })
      .then((r) => setJobId(r.job_id), setError)
      .finally(() => setStarting(false))
  }

  const apply = (chosen: RetimeProposal[]) => {
    if (!result || chosen.length === 0) return
    setError(null)
    setApplying(true)
    applyRetime(dramaId, {
      job_id: result.job_id,
      items: chosen.map((p) => ({ line_id: p.line_id, expected_new_start: p.new_start, expected_new_end: p.new_end })),
    })
      .then((r) => {
        const done = new Set(r.applied)
        setResult((cur) => (cur ? { ...cur, proposals: cur.proposals.filter((p) => !done.has(p.line_id)) } : cur))
        setNote(
          `Re-timed ${r.applied.length} line${r.applied.length === 1 ? '' : 's'}.` +
            (r.skipped.length ? ` ${r.skipped.length} edited since the run, so left alone.` : '') +
            (r.applied.length ? ' Undo from Versions and history.' : ''),
        )
        onChanged()
      }, setError)
      .finally(() => setApplying(false))
  }

  const rows = result?.proposals ?? []

  return (
    <div ref={panelRef}>
      <Section storageKey="review.retimeLines" title="Re-time with Qwen3 aligner" summary="Keeps the words, changes only start and end" openSignal={sectionSignal}>
        <div className="compare-panel" data-testid="retime-lines">
          <p className="muted">
            Fits the ticked lines’ existing text to the audio with the Qwen3 forced aligner, without hearing it again.
            Only start and end change, and nothing changes until you press “Use this”.
          </p>
          <p data-testid="retime-count">{selectedIds.length} line{selectedIds.length === 1 ? '' : 's'} ticked</p>
          <div className="review-actions">
            <button type="button" onClick={start} disabled={busy || applying || !!blocked}>
              {busy ? 'Re-timing…' : 'Re-time ticked lines'}
            </button>
            {active && job && <button type="button" onClick={() => cancelJob(job.job_id).catch(setError)}>Cancel</button>}
          </div>
          {blocked && !busy && <p className="muted" data-testid="retime-blocked">{blocked}</p>}
          <ErrorBanner error={error ?? pollError} onDismiss={() => setError(null)} />
          {active && job && (
            <p className="muted" data-testid="retime-progress">
              {job.progress !== null && <progress value={job.progress} max={1} aria-label="Re-time progress" />}{' '}
              {job.status === 'queued' ? 'Waiting for the GPU…' : job.message || 'Working…'}
            </p>
          )}
          {failure && <p className="error" role="alert" data-testid="retime-failure">{failure}</p>}
          {note && <p className="muted" role="status" data-testid="retime-note">{note}</p>}
          {result && (
            <div data-testid="retime-results">
              {result.device && !result.device_notice && <p className="muted">Aligned on the {result.device}.</p>}
              {result.device_notice && <p className="muted" role="status" data-testid="retime-device-notice">{result.device_notice}</p>}
              {result.partial && <p className="muted" role="status">Stopped early. These are the lines re-timed so far.</p>}
              {result.errors.length > 0 && <p className="muted">Skipped: {result.errors.join('; ')}</p>}
              {rows.length === 0 && <p className="muted">No line’s timing needs to move.</p>}
              {rows.length > 0 && (
                <>
                  <div className="review-actions">
                    <button type="button" onClick={() => apply(rows)} disabled={applying}>Use all shown</button>
                  </div>
                  <div className="table-scroll">
                    <table className="compare-table" data-testid="retime-table">
                      <thead>
                        <tr>
                          <th scope="col">#</th>
                          <th scope="col">Text</th>
                          <th scope="col">Now</th>
                          <th scope="col">Proposed</th>
                          <th scope="col">Use</th>
                        </tr>
                      </thead>
                      <tbody>
                        {rows.map((p) => (
                          <tr key={p.line_id} data-testid="retime-row">
                            <td data-label="Line">{p.number}</td>
                            <td lang="zh" data-label="Text">{p.base_zh}</td>
                            <td data-label="Now">{formatTime(p.start)} – {formatTime(p.end)}</td>
                            <td data-label="Proposed">
                              {formatTime(p.new_start)} – {formatTime(p.new_end)} ({startShift(p)})
                              {p.uncertain && <span className="muted"> · check this one</span>}
                            </td>
                            <td className="compare-use">
                              <button type="button" onClick={() => apply([p])} disabled={applying}>Use this</button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </div>
          )}
        </div>
      </Section>
    </div>
  )
}
