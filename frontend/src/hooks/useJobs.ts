/*
 * One jobs list for the whole signed-in app: the header Jobs popover, the
 * Jobs page and Diagnostics read it from here, so there is one GET and one
 * push subscription and the badge never disagrees with the table.
 *
 * Job changes arrive on the shared push stream; GET /api/jobs is read on
 * mount, after each (re)connect and when the window regains focus. Only while
 * the stream is down does it poll, and only while the tab is visible.
 * A failed read keeps the last list and records the failure in `error`.
 */
import { createContext, useContext, useEffect, useState } from 'react'

import { ApiError } from '../api/client'
import { HIDE_ON } from '../components/jobsMenuState'
import { upsertJob } from '../pages/diagnosticsFormat'
import type { JobRecord } from '../types/jobs'

export interface JobsState {
  /** null until the first read answers. */
  jobs: JobRecord[] | null
  /** The latest failed read, cleared by the next good one. */
  error: unknown
  /** The stream is down and the list is being polled. */
  polling: boolean
  reload: () => void
}

export const JobsContext = createContext<JobsState | null>(null)

export function useJobs(): JobsState {
  const state = useContext(JobsContext)
  if (!state) throw new Error('useJobs needs a JobsProvider')
  return state
}

/** 401, 403 or 404 from the list route: this viewer or server can't list jobs, so the popover hides and the page says why. */
export function jobsRefusal(error: unknown): number | null {
  return error instanceof ApiError && HIDE_ON.includes(error.status) ? error.status : null
}

export interface JobsSync {
  /** Start a GET /api/jobs; returns its token. */
  begin(): number
  /** Note a pushed 'job' (the record) or 'job_gone' (null). */
  push(jobId: string, job: JobRecord | null): void
  /** The list to show for a GET's answer, or null if a newer GET has started. */
  settle(token: number, items: JobRecord[]): JobRecord[] | null
  isCurrent(token: number): boolean
}

/** Orders GETs against pushes. A GET's snapshot can predate a push that
 * arrives while it is in flight (a job finishing), so only the newest GET
 * applies and those pushes are replayed over it; otherwise a done job would
 * flip back to running. */
export function createJobsSync(): JobsSync {
  let seq = 0
  let pushed = new Map<string, JobRecord | null>()
  return {
    begin() {
      pushed = new Map()
      return ++seq
    },
    push(jobId, job) {
      pushed.set(jobId, job)
    },
    settle(token, items) {
      if (token !== seq) return null
      let out = items
      for (const [id, job] of pushed) out = job ? upsertJob(out, job) : out.filter((j) => j.job_id !== id)
      return out
    },
    isCurrent: (token) => token === seq,
  }
}

/** Seconds since the epoch, re-read every second while `live`, for running-job durations. */
export function useNow(live: boolean): number {
  const [now, setNow] = useState(() => Date.now() / 1000)
  useEffect(() => {
    if (!live) return
    setNow(Date.now() / 1000)
    const t = window.setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => window.clearInterval(t)
  }, [live])
  return now
}
