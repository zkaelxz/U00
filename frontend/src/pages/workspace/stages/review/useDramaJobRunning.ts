import { useEffect, useRef } from 'react'

import { useJobs } from '../../../../hooks/useJobs'
import { jobRunsOnDrama } from './reviewLogic'

// True while any job for this drama is queued or running (structure edits
// would be refused with a 409 then). Reads the app-wide jobs list, and asks
// for a fresh one whenever `reloads` changes. No list yet or a failed read
// counts as "no job": the server's own 409 still guards the write.
export function useDramaJobRunning(dramaId: number, reloads: number): boolean {
  const { jobs, reload } = useJobs()
  const seen = useRef(reloads)
  useEffect(() => {
    if (seen.current === reloads) return
    seen.current = reloads
    reload()
  }, [reloads, reload])

  return jobs !== null && jobRunsOnDrama(jobs, dramaId)
}
