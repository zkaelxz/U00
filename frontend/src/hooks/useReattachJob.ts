import { useEffect, useRef } from 'react'

import { getJob } from '../api/jobs'
import { TERMINAL_STATUSES, type JobRecord } from '../types/jobs'

interface ReattachOptions {
  fetchJob?: (id: string) => Promise<JobRecord>
  // Called with the first id (in list order) whose job is still queued or running.
  attach: (id: string) => void
}

// Queued or running, and not a record a dead process left "running" (the
// server flags those as `stale`, on its own clock, so a device clock that is
// off can't hide a live job or revive a dead one).
function isLiveJob(job: JobRecord): boolean {
  return !TERMINAL_STATUSES.includes(job.status) && !job.stale
}

// Framework-free lookup (testable without a DOM). Reads each candidate id
// once; a missing id (404), a failed read, a finished job or a stale record
// is skipped. Returns a cancel function so a lookup never attaches after unmount.
export function reattachActiveJob(ids: readonly string[], opts: ReattachOptions): () => void {
  const { fetchJob = getJob } = opts
  let cancelled = false
  void Promise.allSettled(ids.map((id) => fetchJob(id))).then((results) => {
    if (cancelled) return
    const i = results.findIndex((r) => r.status === 'fulfilled' && isLiveJob(r.value))
    if (i >= 0) opts.attach(ids[i])
  })
  return () => {
    cancelled = true
  }
}

// Stage jobs have fixed per-drama ids (translate_3, dub_3, ...) and keep
// running server-side when the stage unmounts. On mount this finds the
// stage's queued/running job and hands its id to `adopt` (useJobRun's fourth
// value, which ignores it if the stage already started a run), so the stage
// tracks it again through useJob: Start stays disabled and progress resumes.
export function useReattachJob(
  ids: readonly string[],
  adopt: (id: string) => void,
  fetchJob?: (id: string) => Promise<JobRecord>,
) {
  const adoptRef = useRef(adopt)
  const fetchRef = useRef(fetchJob)
  useEffect(() => {
    adoptRef.current = adopt
    fetchRef.current = fetchJob
  })
  // Keyed on the joined ids, so a fresh array each render doesn't re-query.
  const key = ids.join('\n')
  useEffect(() => {
    if (!key) return
    return reattachActiveJob(key.split('\n'), {
      fetchJob: fetchRef.current,
      attach: (id) => adoptRef.current(id),
    })
  }, [key])
}
