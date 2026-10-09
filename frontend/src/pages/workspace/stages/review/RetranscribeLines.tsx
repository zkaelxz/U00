import { useEffect, useRef, useState } from 'react'

import { cancelJob } from '../../../../api/jobs'
import { applyRetranscribeLines, getRetranscribeLinesResult, startRetranscribeLines } from '../../../../api/workspace'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { Section } from '../../../../components/Section'
import { useJob, useJobRun } from '../../../../hooks/useJob'
import { TERMINAL_STATUSES } from '../../../../types/jobs'
import type { RetranscribeManyResult } from '../../../../types/workspace'
import { useLineSelectionContext } from './LineSelectionContext'
import {
  applyItems, applyNote, failureSummary, retranscribeLinesOutcome, retranscribeLinesProblem,
} from './retranscribeLinesLogic'

// Opens the section; with lineIds it also starts the run on exactly those lines
// ("Transcribe this gap", "Add line and transcribe"), without lineIds the section
// just shows the ticked lines. `seq` makes each request distinct.
export interface RetranscribeRequest {
  seq: number
  lineIds: number[] | null
}

// Re-hears the ticked lines' own audio windows in one background job (one model
// load, the title's full-transcribe settings, Fast mode off) and lists what it
// heard next to what is there now. Nothing is written until "Apply selected":
// the server then replaces the source text of those lines that are unchanged
// since the run, clears their English so Translate counts them again, and
// takes a history snapshot first.
export function RetranscribeLines({
  dramaId,
  jobRunning,
  onChanged,
  request,
}: {
  dramaId: number
  jobRunning: boolean
  onChanged: () => void
  request?: RetranscribeRequest | null
}) {
  const { selectedIds } = useLineSelectionContext()
  const panelRef = useRef<HTMLDivElement>(null)
  const [sectionSignal, setSectionSignal] = useState(0)
  const [error, setError] = useState<unknown>(null)
  const [starting, setStarting] = useState(false)
  const [applying, setApplying] = useState(false)
  const [jobId, setJobId, runKey] = useJobRun()
  const [result, setResult] = useState<RetranscribeManyResult | null>(null)
  const [checked, setChecked] = useState<ReadonlySet<number>>(new Set())
  const [failure, setFailure] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  const { job, error: pollError } = useJob(jobId, {
    runKey,
    onDone: (j) => {
      const o = retranscribeLinesOutcome(j)
      if (o.kind === 'none') {
        setFailure(o.text)
        return
      }
      getRetranscribeLinesResult(dramaId).then((r) => {
        setResult(r)
        setChecked(new Set(r.proposals.map((p) => p.line_id)))
      }, setError)
    },
  })

  const active = !!job && !TERMINAL_STATUSES.includes(job.status)
  const busy = starting || active || (!!jobId && !job && !pollError)

  const start = (ids: number[]) => {
    setError(null)
    setResult(null)
    setChecked(new Set())
    setFailure(null)
    setNote(null)
    setStarting(true)
    startRetranscribeLines(dramaId, { line_ids: ids })
      .then((r) => setJobId(r.job_id), setError)
      .finally(() => setStarting(false))
  }

  // Each request opens the section and brings it into view; the run starts
  // once per request even if the effect runs again.
  const handled = useRef(0)
  useEffect(() => {
    if (!request || request.seq === handled.current) return
    handled.current = request.seq
    setSectionSignal((n) => n + 1)
    panelRef.current?.scrollIntoView?.({ block: 'start' })
    if (request.lineIds) start(request.lineIds)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request])

  const problem = retranscribeLinesProblem(selectedIds.length)
  const blocked = problem ?? (jobRunning && !busy ? 'Another job is running on this title. Try again when it finishes.' : null)
  const rows = result?.proposals ?? []
  const chosen = rows.filter((p) => checked.has(p.line_id))

  const toggle = (id: number) =>
    setChecked((cur) => {
      const next = new Set(cur)
      if (!next.delete(id)) next.add(id)
      return next
    })

  const apply = () => {
    if (!result || chosen.length === 0) return
    setError(null)
    setApplying(true)
    const clearing = new Set(chosen.filter((p) => p.had_english).map((p) => p.line_id))
    applyRetranscribeLines(dramaId, { job_id: result.job_id, items: applyItems(rows, checked) })
      .then((r) => {
        const done = new Set(r.applied)
        setResult((cur) => (cur ? { ...cur, proposals: cur.proposals.filter((p) => !done.has(p.line_id)) } : cur))
        setChecked((cur) => new Set([...cur].filter((id) => !done.has(id))))
        setNote(applyNote(r, r.applied.filter((id) => clearing.has(id)).length))
        onChanged()
      }, setError)
      .finally(() => setApplying(false))
  }

  const failed = result ? failureSummary(result.failures) : null

  return (
    <div ref={panelRef}>
      <Section storageKey="review.retranscribeLines" title="Re-transcribe selected lines" summary="Hears the ticked lines again; you choose which text to use" openSignal={sectionSignal}>
        <div className="compare-panel" data-testid="retranscribe-lines">
          <p className="muted">
            Runs speech recognition again on just the ticked lines’ audio, with this title’s transcribe settings, and
            shows what it heard next to what is there now. Nothing changes until you press “Apply selected”; the
            English of each line you apply is cleared so it can be translated again.
          </p>
          <p data-testid="retranscribe-lines-count">{selectedIds.length} line{selectedIds.length === 1 ? '' : 's'} ticked</p>
          <div className="review-actions">
            <button type="button" onClick={() => start(selectedIds)} disabled={busy || applying || !!blocked}>
              {busy ? 'Re-transcribing…' : 'Re-transcribe selected'}
            </button>
            {active && job && <button type="button" onClick={() => cancelJob(job.job_id).catch(setError)}>Cancel</button>}
          </div>
          {blocked && !busy && <p className="muted" data-testid="retranscribe-lines-blocked">{blocked}</p>}
          <ErrorBanner error={error ?? pollError} onDismiss={() => setError(null)} />
          {active && job && (
            <p className="muted" data-testid="retranscribe-lines-progress">
              {job.progress !== null && <progress value={job.progress} max={1} aria-label="Re-transcribe progress" />}{' '}
              {job.status === 'queued' ? 'Waiting for the GPU…' : job.message || 'Working…'}
            </p>
          )}
          {failure && <p className="error" role="alert" data-testid="retranscribe-lines-failure">{failure}</p>}
          {note && <p className="muted" role="status" data-testid="retranscribe-lines-note">{note}</p>}
          {result && (
            <div data-testid="retranscribe-lines-results">
              {result.device_notice && <p className="muted" role="status">{result.device_notice}</p>}
              {failed && <p className="muted" data-testid="retranscribe-lines-failed">Nothing to propose for: {failed}.</p>}
              {result.unchanged_count > 0 && (
                <p className="muted">{result.unchanged_count} line{result.unchanged_count === 1 ? '' : 's'} heard the same text.</p>
              )}
              {result.truncated && <p className="muted" role="status">Some proposals were left out to keep memory use down. Run the rest again.</p>}
              {rows.length === 0 && !note && <p className="muted">No line’s text needs to change.</p>}
              {rows.length > 0 && (
                <>
                  <div className="review-actions">
                    <button type="button" onClick={apply} disabled={applying || chosen.length === 0}>
                      Apply selected ({chosen.length})
                    </button>
                    <button type="button" onClick={() => setChecked(new Set(rows.map((p) => p.line_id)))} disabled={applying}>Select all</button>
                    <button type="button" onClick={() => setChecked(new Set())} disabled={applying}>Select none</button>
                  </div>
                  <div className="table-scroll">
                    <table className="compare-table" data-testid="retranscribe-lines-table">
                      <thead>
                        <tr>
                          <th scope="col">Use</th>
                          <th scope="col">#</th>
                          <th scope="col">Now</th>
                          <th scope="col">Heard</th>
                        </tr>
                      </thead>
                      <tbody>
                        {rows.map((p) => (
                          <tr key={p.line_id} data-testid="retranscribe-lines-row">
                            <td className="compare-use">
                              <input
                                type="checkbox"
                                checked={checked.has(p.line_id)}
                                onChange={() => toggle(p.line_id)}
                                disabled={applying}
                                aria-label={`Use the new text for line #${p.number}`}
                              />
                            </td>
                            <td data-label="Line">{p.number}</td>
                            <td lang="zh" data-label="Now">{p.base_zh || <span className="muted">(empty)</span>}</td>
                            <td lang="zh" data-label="Heard">
                              {p.proposed_zh}
                              {p.had_english && <span className="muted"> · clears its English</span>}
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
