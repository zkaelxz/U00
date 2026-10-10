// The one GET /api/jobs and push subscription behind useJobs (hooks/useJobs.ts).
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'

import { listJobs } from '../api/jobs'
import { JOBS_POLL_MS } from '../components/jobsMenuState'
import { upsertJob } from '../pages/diagnosticsFormat'
import type { JobRecord } from '../types/jobs'
import { createJobsSync, JobsContext } from './useJobs'
import { useEventStream } from './useEventStream'

export function JobsProvider({ children }: { children: ReactNode }) {
  const [jobs, setJobs] = useState<JobRecord[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  const sync = useRef(createJobsSync()).current

  const stream = useEventStream((type, data) => {
    const pushed = data as Partial<JobRecord> | null
    if (!pushed?.job_id || (type !== 'job' && type !== 'job_gone')) return
    sync.push(pushed.job_id, type === 'job' ? (pushed as JobRecord) : null)
    setJobs((cur) => {
      if (cur === null) return cur
      return type === 'job' ? upsertJob(cur, pushed as JobRecord) : cur.filter((j) => j.job_id !== pushed.job_id)
    })
  })
  const polling = stream.mode === 'poll'

  const reload = useCallback(() => {
    const token = sync.begin()
    listJobs().then(
      (r) => {
        const next = sync.settle(token, Array.isArray(r?.items) ? r.items : [])
        if (next === null) return
        setJobs(next)
        setError(null)
      },
      (e: unknown) => {
        if (sync.isCurrent(token)) setError(e)
      },
    )
  }, [sync])

  useEffect(() => {
    reload()
    const timer = polling
      ? window.setInterval(() => {
          if (document.visibilityState !== 'hidden') reload()
        }, JOBS_POLL_MS)
      : undefined
    window.addEventListener('focus', reload)
    return () => {
      window.clearInterval(timer)
      window.removeEventListener('focus', reload)
    }
  }, [reload, polling, stream.syncs])

  const value = useMemo(() => ({ jobs, error, polling, reload }), [jobs, error, polling, reload])
  return <JobsContext.Provider value={value}>{children}</JobsContext.Provider>
}
