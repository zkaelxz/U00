import { useEffect, useState } from 'react'

import { withSignal } from '../../api/client'
import { getJobStages } from '../../api/jobs'
import type { JobStageTimings } from '../../types/jobs'
import { NO_STAGE_TIMING, STAGE_TIMING_UNAVAILABLE, runsNote, stageRows, stageTotalLine } from './stageBreakdown'

type Load = { state: 'loading' } | { state: 'error' } | { state: 'ok'; data: JobStageTimings }

/**
 * "Time by stage" for one Job history entry: the latest run's stages with a
 * bar for each one's share of the total. Mounted on first open, so it loads
 * once per entry rather than for every row in the list.
 */
export function JobStagesPanel({ jobId }: { jobId: string }) {
  const [load, setLoad] = useState<Load>({ state: 'loading' })
  useEffect(() => {
    const ctl = new AbortController()
    getJobStages(jobId, withSignal(ctl.signal))
      .then((data) => setLoad({ state: 'ok', data }))
      .catch(() => { if (!ctl.signal.aborted) setLoad({ state: 'error' }) })
    return () => ctl.abort()
  }, [jobId])

  if (load.state === 'loading') return <p className="muted" aria-live="polite">Loading stage timing…</p>
  if (load.state === 'error') return <p className="muted">{STAGE_TIMING_UNAVAILABLE}</p>
  const runs = load.data.runs
  if (runs.length === 0) return <p className="muted">{NO_STAGE_TIMING}</p>
  const run = runs[0]
  const note = runsNote(runs.length)
  return (
    <div className="stage-times">
      <h4 className="stage-times-title">Time by stage</h4>
      <ul aria-label="Time by stage">
        {stageRows(run).map((r) => (
          <li key={r.key}>
            <span className="stage-times-name">{r.name}</span>
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
      <p className="stage-times-total">{stageTotalLine(run)}</p>
      {note && <p className="muted">{note}</p>}
    </div>
  )
}
