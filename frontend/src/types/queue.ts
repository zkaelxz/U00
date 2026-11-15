// /api/queue: the user-managed job queue on the Jobs page (api/schemas/queue.py).
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

export type QueueStep = 'transcribe' | 'translate' | 'export'
// The download item exists only in a Quick video translate run, and names (the glossary scan), import (a
// novel link), text and lines only in a title page's one-button run; all are shown, never picked.
export type QueueItemStep = 'download' | 'names' | 'import' | 'text' | 'lines' | QueueStep
export type QueueItemStatus = 'waiting' | 'running' | 'done' | 'failed' | 'skipped' | 'interrupted' | 'cancelled'
export type QueueItem = Schemas['QueueItem']

export type QueueSchedule = Schemas['QueueSchedule']

export type QueueBulkAdded = Schemas['QueueBulkAdded']

export type QueueState = Schemas['QueueState']

// GET /api/queue/settings: what a queued step will use for a title, read-only.
export type QueueSettingRow = Schemas['QueueSettingRow']

export type QueueSettingValue = string | number | boolean

export type QueueSettingField = Schemas['QueueSettingField']

// What one queued item changes from the title's saved settings; absent keys follow the title.
export type QueueSettingEdits = Record<string, QueueSettingValue>

export type QueueStepSettings = Schemas['QueueStepSettings']

// GET/POST /api/queue/title-flow/{id}: how far a title's one-button run got.
export type TitleFlowRun = Schemas['TitleFlowRun']
