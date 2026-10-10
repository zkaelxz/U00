// Mirrors api/schemas/timing_check.py.

export interface TimingCheckLastRun {
  checked_at: string
  flagged: number
  // Set when nothing was judged (no speech found in the audio at all).
  notice: string | null
}

// A flagged line's corrected times; start/end are the times the check saw.
export interface TimingSuggestion {
  line_id: number
  start: number
  end: number
  new_start: number
  new_end: number
}

export interface TimingCheckStatus {
  job_id: string
  status: string
  progress: number | null
  message: string
  result: Record<string, unknown> | null
  last_check: TimingCheckLastRun | null
  suggestions: TimingSuggestion[]
}

export interface TimingSnapResult {
  snapped: number
  stale_ids: number[]
  history_id: number | null
}
