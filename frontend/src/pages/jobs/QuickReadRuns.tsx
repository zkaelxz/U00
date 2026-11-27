/* Quick read runs still working on the PC, with Drop: the pages not started are not worked on. */
import { useCallback, useEffect, useState } from 'react'

import { dropAllQuickReads, dropQuickRead, listQuickReads, type QuickReadRun } from '../../api/jobs'
import { ErrorBanner } from '../../components/ErrorBanner'
import { buttonClass } from '../../components/uiClasses'
import { useInterval } from '../../hooks/useInterval'
import { runLabel } from './quickReadLabel'

const POLL_MS = 4000

export function QuickReadRuns() {
  const [runs, setRuns] = useState<QuickReadRun[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  const load = useCallback(async () => {
    try {
      setRuns((await listQuickReads()).items)
      setError(null)
    } catch (e) {
      setError(e)
    }
  }, [])
  useEffect(() => {
    const first = window.setTimeout(() => void load(), 0)
    return () => window.clearTimeout(first)
  }, [load])
  useInterval(() => void load(), POLL_MS, { pauseWhenHidden: true })
  async function drop(action: () => Promise<unknown>) {
    try {
      await action()
    } catch (e) {
      setError(e)
    }
    void load()
  }
  return (
    <section data-testid="quick-read-runs">
      {error !== null && <ErrorBanner error={error} />}
      {runs !== null && runs.length === 0 && <p className="muted" role="status">No Quick read is working right now.</p>}
      {runs !== null && runs.length > 0 && (
        <>
          <ul className="quick-read-list">
            {runs.map((run) => (
              <li key={run.run_id} data-testid="quick-read-run">
                <strong>{runLabel(run)}</strong>
                <span className="muted"> {run.pages_left} of {run.pages_received} pages left</span>
                <button type="button" className={buttonClass('danger')} onClick={() => void drop(() => dropQuickRead(run.run_id))}>Drop</button>
              </li>
            ))}
          </ul>
          <button type="button" className={buttonClass('danger')} onClick={() => void drop(dropAllQuickReads)}>Drop all</button>
        </>
      )}
    </section>
  )
}
