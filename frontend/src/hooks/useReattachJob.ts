import { useEffect, useRef } from 'react'

import { getJob } from '../api/jobs'
import { TERMINAL_STATUSES, type JobRecord } from '../types/jobs'

export interface ReattachOptions {
  fetchJob?: (id: string) => Promise<JobRecord>
  // Called with the first id (in list order) whose job is still queued or running.
  attach: (id: string) => void
  // True once the stage has started a run of its own; that run wins.
  started: () => boolean
}

// Framework-free lookup (testable without a DOM). Reads each candidate id
// once; a missing id (404), a failed read or a finished job is skipped.
// Returns a cancel function so a stale lookup never attaches after unmount.
export function reattachActiveJob(ids: readonly string[], opts: ReattachOptions): () => void {
  const { fetchJob = getJob } = opts
  let cancelled = false
  void Promise.allSettled(ids.map((id) => fetchJob(id))).then((results) => {
    if (cancelled || opts.started()) return
    const i = results.findIndex((r) => r.status === 'fulfilled' && !TERMINAL_STATUSES.includes(r.value.status))
    if (i >= 0) opts.attach(ids[i])
  })
  return () => {
    cancelled = true
  }
}

// Stage jobs have fixed per-drama ids (translate_3, dub_3, ...) and keep
// running server-side when the stage unmounts. On mount this finds the
// stage's queued/running job and hands its id to `attach` (a useJobRun
// setter), so the stage tracks it again through useJob like a run it started
// itself: Start stays disabled and progress resumes.
export function useReattachJob(
  ids: readonly string[],
  jobId: string | null,
  attach: (id: string) => void,
  fetchJob?: (id: string) => Promise<JobRecord>,
) {
  const startedRef = useRef(jobId !== null)
  const attachRef = useRef(attach)
  const fetchRef = useRef(fetchJob)
  useEffect(() => {
    startedRef.current = jobId !== null
    attachRef.current = attach
    fetchRef.current = fetchJob
  })
  // Keyed on the joined ids, so a fresh array each render doesn't re-query.
  const key = ids.join('\n')
  useEffect(() => {
    if (!key) return
    return reattachActiveJob(key.split('\n'), {
      fetchJob: fetchRef.current,
      attach: (id) => attachRef.current(id),
      started: () => startedRef.current,
    })
  }, [key])
}
