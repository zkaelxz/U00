import { useCallback, useEffect, useRef, useState } from 'react'

import { ApiError } from '../../api/client'
import { listJobs } from '../../api/jobs'
import type { JobRecord } from '../../types/jobs'
import { FLASH_MS, JOB_POLL_MS, activeJobsFor, finishedFlash, type PillFlash } from './jobPill'

// A refusal or a server without the route hides the pill; any other failure keeps the last list.
const HIDE_ON = [401, 403, 404]

// One GET /api/jobs every 3 s while the tab is visible (and on focus). The
// jobs on this title are kept; when one ends, `flash` shows its outcome for
// 5 s and onFinished runs once so the drama and counts reload.
export function useDramaJobs(dramaId: number, onFinished: () => void) {
  const [jobs, setJobs] = useState<JobRecord[]>([])
  const [flash, setFlash] = useState<PillFlash | null>(null)
  const [hidden, setHidden] = useState(false)
  const prevActive = useRef<Set<string>>(new Set())
  const hiddenRef = useRef(false)
  const inflight = useRef(false)
  const finished = useRef(onFinished)
  finished.current = onFinished

  const load = useCallback(() => {
    if (hiddenRef.current || inflight.current) return
    inflight.current = true
    listJobs().then(
      (r) => {
        inflight.current = false
        const items = Array.isArray(r?.items) ? r.items : []
        const outcome = finishedFlash(prevActive.current, items, dramaId)
        prevActive.current = new Set(activeJobsFor(items, dramaId).map((j) => j.job_id))
        setJobs(items)
        if (outcome) {
          setFlash(outcome)
          finished.current()
        }
      },
      (e: unknown) => {
        inflight.current = false
        if (e instanceof ApiError && HIDE_ON.includes(e.status)) {
          hiddenRef.current = true
          setHidden(true)
        }
      },
    )
  }, [dramaId])

  useEffect(() => {
    load()
    const timer = window.setInterval(() => {
      if (document.visibilityState !== 'hidden') load()
    }, JOB_POLL_MS)
    window.addEventListener('focus', load)
    return () => {
      window.clearInterval(timer)
      window.removeEventListener('focus', load)
    }
  }, [load])

  useEffect(() => {
    if (!flash) return
    const t = window.setTimeout(() => setFlash(null), FLASH_MS)
    return () => window.clearTimeout(t)
  }, [flash])

  return { active: activeJobsFor(jobs, dramaId), flash, hidden, reload: load }
}
