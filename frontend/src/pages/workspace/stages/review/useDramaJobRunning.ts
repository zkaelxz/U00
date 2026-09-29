import { useEffect, useState } from 'react'

import { listJobs } from '../../../../api/jobs'
import { jobRunsOnDrama } from './reviewLogic'

const POLL_MS = 5000

// True while any job for this drama is queued or running (structure edits
// would be refused with a 409 then). Polls GET /api/jobs while the page is
// visible, and again whenever `reloads` changes. A failed poll counts as "no
// job": the server's own 409 still guards the write.
export function useDramaJobRunning(dramaId: number, reloads: number): boolean {
  const [running, setRunning] = useState(false)

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const tick = () => {
      if (document.visibilityState === 'hidden') {
        timer = setTimeout(tick, POLL_MS)
        return
      }
      listJobs().then(
        (r) => !cancelled && setRunning(jobRunsOnDrama(r.items, dramaId)),
        () => !cancelled && setRunning(false),
      ).finally(() => {
        if (!cancelled) timer = setTimeout(tick, POLL_MS)
      })
    }
    tick()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [dramaId, reloads])

  return running
}
