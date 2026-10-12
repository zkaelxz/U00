// /api/queue: the user-managed job queue on the Jobs page (api/schemas/queue.py).
export type QueueStep = 'transcribe' | 'translate' | 'export'
export type QueueItemStatus = 'waiting' | 'running' | 'done' | 'failed' | 'skipped' | 'interrupted' | 'cancelled'
export type QueueRunState = 'idle' | 'running' | 'paused'

export interface QueueItem {
  id: number
  drama_id: number
  title: string
  step: QueueStep
  status: QueueItemStatus
  reason: string | null
  job_id: string | null
  engine: string | null
  paid: boolean
  mine: boolean
  added_at: number | null
  started_at: number | null
  finished_at: number | null
}

export interface QueueSchedule {
  time: string
  start_at: number
  missed: boolean
}

export interface QueueState {
  version: number
  state: QueueRunState
  stop_on_failure: boolean
  schedule: QueueSchedule | null
  notice: string | null
  max_items: number
  can_control: boolean
  monthly_cap_usd: number
  items: QueueItem[]
}

// GET /api/queue/settings: what a queued step will use for a title, read-only.
export interface QueueSettingRow {
  label: string
  value: string
  // Engine id on the engine row; the page words it.
  engine?: string | null
}

export interface QueueStepSettings {
  drama_id: number
  step: QueueStep
  title: string
  rows: QueueSettingRow[]
}
