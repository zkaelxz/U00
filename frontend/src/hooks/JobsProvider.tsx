// The one GET /api/jobs and push subscription behind useJobs (hooks/useJobs.ts).
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'

import { listJobs } from '../api/jobs'
import { JOBS_POLL_MS } from '../components/jobsMenuState'
import { upsertJob } from '../pages/diagnosticsFormat'
import type { JobRecord } from '../types/jobs'
import { JobsContext } from './useJobs'
import { useEventStream } from './useEventStream'

export function JobsProvider({ children }: { children: ReactNode }) {
  const [jobs, setJobs] = useState<JobRecord[] | null>(null)
  const [error, setError] = useState<unknown>(null)

  const stream = useEventStream((type, data) => {
    const pushed = data as Partial<JobRecord> | null
    if (!pushed?.job_id || (type !== 'job' && type !== 'job_gone')) return
    setJobs((cur) => {
      if (cur === null) return cur
      return type === 'job' ? upsertJob(cur, pushed as JobRecord) : cur.filter((j) => j.job_id !== pushed.job_id)
    })
  })
  const polling = stream.mode === 'poll'

  const reload = useCallback(() => {
    listJobs().then(
      (r) => {
        setJobs(Array.isArray(r?.items) ? r.items : [])
        setError(null)
      },
      (e: unknown) => setError(e),
    )
  }, [])

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
