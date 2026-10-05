import { useEffect, useRef, useState } from 'react'

import { jobsRefusal, useJobs } from '../../hooks/useJobs'
import { FLASH_MS, activeJobsFor, finishedFlash, type PillFlash } from './jobPillState'

// The jobs on this title come from the app-wide list; when one ends, `flash`
// shows its outcome for 5 s and onFinished runs once so the drama and counts reload.
export function useDramaJobs(dramaId: number, onFinished: () => void) {
  const { jobs, error, reload } = useJobs()
  const [flash, setFlash] = useState<PillFlash | null>(null)
  const prevActive = useRef<Set<string>>(new Set())
  const finished = useRef(onFinished)
  finished.current = onFinished

  useEffect(() => {
    if (!jobs) return
    const outcome = finishedFlash(prevActive.current, jobs, dramaId)
    prevActive.current = new Set(activeJobsFor(jobs, dramaId).map((j) => j.job_id))
    if (outcome) {
      setFlash(outcome)
      finished.current()
    }
  }, [jobs, dramaId])

  useEffect(() => {
    if (!flash) return
    const t = window.setTimeout(() => setFlash(null), FLASH_MS)
    return () => window.clearTimeout(t)
  }, [flash])

  // A refusal or a server without the route hides the pill; any other failure keeps the last list.
  return { active: activeJobsFor(jobs ?? [], dramaId), flash, hidden: jobsRefusal(error) !== null, reload }
}
