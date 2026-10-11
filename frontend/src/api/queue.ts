import type { QueueState, QueueStep } from '../types/queue'
import { getJson, postJson } from './client'

type Fetch = typeof fetch

export const getQueue = (f?: Fetch) => getJson<QueueState>('/api/queue', f)
export const addQueueItem = (dramaId: number, step: QueueStep, f?: Fetch) =>
  postJson<QueueState>('/api/queue/items', { drama_id: dramaId, step }, f)
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
