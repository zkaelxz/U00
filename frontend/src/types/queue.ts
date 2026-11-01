// /api/queue: the user-managed job queue on the Jobs page (api/schemas/queue.py).
export type QueueStep = 'transcribe' | 'translate' | 'export'
// The download item exists only in a Quick video translate run; it is shown, never picked.
export type QueueItemStep = 'download' | QueueStep
export type QueueItemStatus = 'waiting' | 'running' | 'done' | 'failed' | 'skipped' | 'interrupted' | 'cancelled'
type QueueRunState = 'idle' | 'running' | 'paused'

export interface QueueItem {
  id: number
  drama_id: number
  title: string
  step: QueueItemStep
  status: QueueItemStatus
  reason: string | null
  job_id: string | null
  engine: string | null
  paid: boolean
  // What this item changes from the title's saved settings.
  changed_settings: QueueSettingRow[]
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

export interface QueueBulkAdded {
  queue: QueueState
  added: number[]
  skipped: { drama_id: number; reason: string }[]
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

export type QueueSettingValue = string | number | boolean

export interface QueueSettingField {
  key: string
  label: string
  kind: 'select' | 'number' | 'toggle'
  // The title's saved value: what the field starts at.
  value: QueueSettingValue
  options: { value: string; label: string }[]
  min: number | null
  max: number | null
  // Why the caller can't change it (a paid option without permission).
  locked: string | null
}

// What one queued item changes from the title's saved settings; absent keys follow the title.
export type QueueSettingEdits = Record<string, QueueSettingValue>

export interface QueueStepSettings {
  drama_id: number
  step: QueueStep
  title: string
  rows: QueueSettingRow[]
  fields: QueueSettingField[]
}
