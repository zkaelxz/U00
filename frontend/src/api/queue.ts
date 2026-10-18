import type { QueueBulkAdded, QueueSettingEdits, QueueState, QueueStep, QueueStepSettings } from '../types/queue'
import { getJson, postJson } from './client'

type Fetch = typeof fetch

export const getQueue = (f?: Fetch) => getJson<QueueState>('/api/queue', f)
export const getQueueStepSettings = (dramaId: number, step: QueueStep, f?: Fetch) =>
  getJson<QueueStepSettings>(`/api/queue/settings?drama_id=${dramaId}&step=${step}`, f)
// settings: this item's own changes to the title's saved Transcribe options; the title is not changed.
export const addQueueItem = (dramaId: number, step: QueueStep, settings?: QueueSettingEdits, f?: Fetch) =>
  postJson<QueueState>('/api/queue/items', { drama_id: dramaId, step, ...(settings ? { settings } : {}) }, f)
// Transcribe, Translate and Export for one title, added together in that order.
export const addQueueAllSteps = (dramaId: number, f?: Fetch) =>
  postJson<QueueState>('/api/queue/items/all-steps', { drama_id: dramaId }, f)
// One step (or 'all') for several titles in one request; a refused title is skipped with its reason.
export const addQueueBulk = (dramaIds: number[], step: QueueStep | 'all', f?: Fetch) =>
  postJson<QueueBulkAdded>('/api/queue/items/bulk', { drama_ids: dramaIds, step }, f)
export const moveQueueItem = (id: number, direction: 'up' | 'down', f?: Fetch) =>
  postJson<QueueState>(`/api/queue/items/${id}/move`, { direction }, f)
export const removeQueueItem = (id: number, f?: Fetch) =>
  postJson<QueueState>(`/api/queue/items/${id}/remove`, undefined, f)
export const retryQueueItem = (id: number, f?: Fetch) =>
  postJson<QueueState>(`/api/queue/items/${id}/retry`, undefined, f)
export const clearFinishedQueueItems = (f?: Fetch) => postJson<QueueState>('/api/queue/clear-finished', undefined, f)
export const startQueue = (f?: Fetch) => postJson<QueueState>('/api/queue/start', undefined, f)
export const pauseQueue = (f?: Fetch) => postJson<QueueState>('/api/queue/pause', undefined, f)
// time "HH:MM" on the PC's clock, or null to clear the schedule (a missed one too).
export const setQueueSchedule = (time: string | null, f?: Fetch) =>
  postJson<QueueState>('/api/queue/schedule', { time }, f)
export const setQueuePolicy = (stopOnFailure: boolean, f?: Fetch) =>
  postJson<QueueState>('/api/queue/policy', { stop_on_failure: stopOnFailure }, f)
