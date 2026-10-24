import { useEffect, useState } from 'react'

import { withSignal } from '../../api/client'
import { getJobStages } from '../../api/jobs'
import type { JobStageTimings } from '../../types/jobs'
import { NO_STAGE_TIMING, STAGE_TIMING_UNAVAILABLE, recordedStageRows, runsNote, stageRows, stageTotalLine } from './stageBreakdown'

// The log of a running job re-reads this often; a hidden tab skips the read.
const STAGE_POLL_MS = 5_000

type Load = { state: 'loading' } | { state: 'error' } | { state: 'ok'; data: JobStageTimings }

/**
 * "Time by stage" for one Job history entry: the latest run's stages with a
 * bar for each one's share of the total. Mounted on first open, so it loads
 * once per entry rather than for every row in the list.
 */
export function JobStagesPanel({ jobId, running = false, step, result }: { jobId: string; running?: boolean; step?: string | null; result?: Record<string, unknown> | null }) {
  const [load, setLoad] = useState<Load>({ state: 'loading' })
  useEffect(() => {
    const ctl = new AbortController()
    const read = () =>
      getJobStages(jobId, withSignal(ctl.signal))
        .then((data) => setLoad({ state: 'ok', data }))
        // A failed refresh keeps the last log on screen; only the first read shows the error.
        .catch(() => { if (!ctl.signal.aborted) setLoad((cur) => (cur.state === 'ok' ? cur : { state: 'error' })) })
    void read()
    const timer = running ? window.setInterval(() => { if (!document.hidden) void read() }, STAGE_POLL_MS) : undefined
    return () => {
      ctl.abort()
      window.clearInterval(timer)
    }
  }, [jobId, running])

  // A transcription records its own steps in its result; they show even when the log is empty or unreadable.
  const recorded = recordedStageRows(result)
  const recordedList = recorded.length > 0 && (
    <div className="stage-times">
      <h3 className="stage-times-title">Recorded steps</h3>
      <ul aria-label="Recorded steps">
        {recorded.map((r) => (
          <li key={r.key}>
            <span className="stage-times-name">{r.name}</span>
            <span className="stage-times-nums">{r.duration}</span>
            <span className="stage-times-bar" aria-hidden="true"><span style={{ width: `${r.share}%` }} /></span>
          </li>
        ))}
      </ul>
    </div>
  )
  if (load.state === 'loading') return <p className="muted" aria-live="polite">Loading stage timing…</p>
  if (load.state === 'error') return <><p className="muted">{STAGE_TIMING_UNAVAILABLE}</p>{recordedList}</>
  const runs = load.data.runs
  if (runs.length === 0) return recordedList || <p className="muted">{NO_STAGE_TIMING}</p>
  const run = runs[0]
  const note = runsNote(runs.length)
  return (
    <div className="stage-times">
      <h3 className="stage-times-title">{running ? 'Steps so far' : 'Time by stage'}</h3>
      <ul aria-label={running ? 'Steps so far' : 'Time by stage'}>
        {stageRows(run).map((r) => (
          <li key={r.key}>
            <span className="stage-times-name">
              {r.at && <time className="muted stage-times-at">{r.at}</time>} {r.name}
            </span>
            <span className="stage-times-nums">
              {r.duration}
              {r.cost && <span className="muted"> · {r.cost}</span>}
            </span>
            <span className="stage-times-bar" aria-hidden="true">
              <span style={{ width: `${r.share}%` }} />
            </span>
          </li>
        ))}
      </ul>
      {running && step && <p className="muted" aria-live="polite">Now: {step}</p>}
      <p className="stage-times-total">{stageTotalLine(run)}</p>
      {note && <p className="muted">{note}</p>}
      {recordedList}
    </div>
  )
}
