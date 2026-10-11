// Pure helpers for JobPanel's "no progress" warning. The server flags a
// running job as `stalled` on its own clock (background_jobs.job_may_be_stalled)
// and appends STALL_NOTE to its message; this watch counts the same thing in
// the browser, so a job that stops moving after the page loaded is caught
// without waiting for the next record read.
import type { JobRecord } from '../../../types/jobs'

export const STUCK_MINUTES = 5
// Whisper and pyannote load models in silence: a transcribe or diarize run
// gets twice as long before it is called stuck.
export const STUCK_MINUTES_SILENT = 10
export const STUCK_TICK_MS = 15_000

// services/jobs_service.py _with_live_progress appends this to the message.
export const STALL_NOTE = 'No update for a while: this job may be stalled.'

export const stuckMinutesFor = (jobId: string): number =>
  /^(transcribe|diarize)_/.test(jobId) ? STUCK_MINUTES_SILENT : STUCK_MINUTES

// The message without the server's stall note: the warning line says it once.
export const stripStallNote = (message: string): string => message.replace(STALL_NOTE, '').trim()

// What counts as progress: the percent, the message and the record's
// updated_at. The "(elapsed 1m 05s)" ticker a silent stage keeps fresh and the
// stall note are not progress, so they are left out of the key.
export function progressKey(job: Pick<JobRecord, 'progress' | 'message' | 'updated_at'>): string {
  const message = stripStallNote(job.message ?? '').replace(/\s*\(elapsed [^)]*\)/g, '')
  return `${job.progress ?? ''}\u0000${message}\u0000${job.updated_at ?? ''}`
}

export interface StuckInfo {
  // Whole minutes since the browser last saw the job change.
  minutes: number
  // Past the job's allowance, or flagged stalled by the server.
  stuck: boolean
}

interface WatchOptions {
  intervalMs?: number
  now?: () => number
}

// Framework-free watch (testable with fake timers). Reads the job every tick
// and after `update`; reports to onChange only when the answer changes, and
// null once the job is gone or finished. Returns {update, stop}.
export function watchStuck(
  read: () => JobRecord | null,
  onChange: (info: StuckInfo | null) => void,
  { intervalMs = STUCK_TICK_MS, now = () => Date.now() / 1000 }: WatchOptions = {},
): { update: () => void; stop: () => void } {
  let key: string | null = null
  let since = now()
  let last: string | null = null
  const check = () => {
    const job = read()
    const t = now()
    if (!job || job.status === 'done' || job.status === 'error' || job.status === 'cancelled') {
      key = null
      if (last !== null) {
        last = null
        onChange(null)
      }
      return
    }
    const k = progressKey(job)
    if (k !== key) {
      key = k
      since = t
    }
    const minutes = Math.floor(Math.max(0, t - since) / 60)
    const info = { minutes, stuck: job.stalled === true || minutes >= stuckMinutesFor(job.job_id) }
    const tag = `${info.minutes}:${info.stuck}`
    if (tag !== last) {
      last = tag
      onChange(info)
    }
  }
  check()
  const timer = setInterval(check, intervalMs)
  return { update: check, stop: () => clearInterval(timer) }
}

// The warning line: the browser's own count when it has one, else the
// server's word for it (the page may have opened after the job stopped moving).
export function stuckText(info: StuckInfo, jobId: string): string {
  const measured = info.minutes >= stuckMinutesFor(jobId)
  return `No progress for ${measured ? `${info.minutes} min` : 'a while'}. It may be stuck.`
}
