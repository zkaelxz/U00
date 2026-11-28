import type { NovelFlowStart, QueueBulkAdded, QueueSettingEdits, QueueState, QueueStep, QueueStepSettings, TitleFlowRun } from '../types/queue'
import { getJson, postJson, type Fetch } from './client'

export const getQueue = (f?: Fetch) => getJson<QueueState>('/api/queue', f)
// model and batchSize (Translate): what the item would change, so the cost row prices them.
export const getQueueStepSettings = (dramaId: number, step: QueueStep, own?: { model?: string; batchSize?: number }, f?: Fetch) => {
  const extra = `${own?.model ? `&model=${encodeURIComponent(own.model)}` : ''}${own?.batchSize ? `&batch_size=${own.batchSize}` : ''}`
  return getJson<QueueStepSettings>(`/api/queue/settings?drama_id=${dramaId}&step=${step}${extra}`, f)
}
// settings: this item's own changes to the title's saved Transcribe options; the title is not changed.
// tonight: the item waits for the night window and an idle PC.
export const addQueueItem = (dramaId: number, step: QueueStep, settings?: QueueSettingEdits, tonight = false, f?: Fetch) =>
  postJson<QueueState>('/api/queue/items', { drama_id: dramaId, step, ...(settings ? { settings } : {}), ...(tonight ? { tonight } : {}) }, f)
// Transcribe, Translate and Export for one title, added together in that order.
export const addQueueAllSteps = (dramaId: number, tonight = false, f?: Fetch) =>
  postJson<QueueState>('/api/queue/items/all-steps', { drama_id: dramaId, ...(tonight ? { tonight } : {}) }, f)
// One step (or 'all') for several titles in one request; a refused title is skipped with its reason.
export const addQueueBulk = (dramaIds: number[], step: QueueStep | 'all', tonight = false, f?: Fetch) =>
  postJson<QueueBulkAdded>('/api/queue/items/bulk', { drama_ids: dramaIds, step, ...(tonight ? { tonight } : {}) }, f)
// false is Run now: the item waits only for its turn.
export const setQueueItemTonight = (id: number, tonight: boolean, f?: Fetch) =>
  postJson<QueueState>(`/api/queue/items/${id}/tonight`, { tonight }, f)
// The night window ("HH:MM" on the PC's clock) and the nightly self-check switch.
export const setQueueNight = (night: { start: string; end: string; self_check: boolean }, f?: Fetch) =>
  postJson<QueueState>('/api/queue/night', night, f)
export const moveQueueItem = (id: number, direction: 'up' | 'down', f?: Fetch) =>
  postJson<QueueState>(`/api/queue/items/${id}/move`, { direction }, f)
export const removeQueueItem = (id: number, f?: Fetch) =>
  postJson<QueueState>(`/api/queue/items/${id}/remove`, undefined, f)
export const retryQueueItem = (id: number, f?: Fetch) =>
  postJson<QueueState>(`/api/queue/items/${id}/retry`, undefined, f)
export const holdQueueItem = (id: number, f?: Fetch) => postJson<QueueState>(`/api/queue/items/${id}/hold`, undefined, f)
export const resumeQueueItem = (id: number, f?: Fetch) => postJson<QueueState>(`/api/queue/items/${id}/resume`, undefined, f)
export const cancelQueueItem = (id: number, f?: Fetch) => postJson<QueueState>(`/api/queue/items/${id}/cancel`, undefined, f)
export const clearFinishedQueueItems = (f?: Fetch) => postJson<QueueState>('/api/queue/clear-finished', undefined, f)
export const startQueue = (f?: Fetch) => postJson<QueueState>('/api/queue/start', undefined, f)
export const pauseQueue = (f?: Fetch) => postJson<QueueState>('/api/queue/pause', undefined, f)
// time "HH:MM" on the PC's clock, or null to clear the schedule (a missed one too).
export const setQueueSchedule = (time: string | null, f?: Fetch) =>
  postJson<QueueState>('/api/queue/schedule', { time }, f)
// Only the field given changes.
export const setQueuePolicy = (policy: { stop_on_failure?: boolean; overlap_online_translate?: boolean }, f?: Fetch) =>
  postJson<QueueState>('/api/queue/policy', policy, f)

// A title's one-button run (transcribe, then find names), carried on by the PC with the page closed.
export const getTitleFlow = (dramaId: number, f?: Fetch) => getJson<TitleFlowRun>(`/api/queue/title-flow/${dramaId}`, f)
export const startTitleFlow = (dramaId: number, f?: Fetch) => postJson<TitleFlowRun>(`/api/queue/title-flow/${dramaId}`, undefined, f)
// A novel's link run (import, text, lines, then find names); the link is read only while no chapter is saved.
export const getNovelFlow = (dramaId: number, f?: Fetch) => getJson<TitleFlowRun>(`/api/queue/novel-flow/${dramaId}`, f)
export const startNovelFlow = (dramaId: number, url: string, lines: Omit<NovelFlowStart, 'url'> = {}, f?: Fetch) =>
  postJson<TitleFlowRun>(`/api/queue/novel-flow/${dramaId}`, { url, ...lines }, f)
// The Add box's run after its upload: follow the upload's transcription, then translate with the box's choices.
export const getFileRun = (dramaId: number, f?: Fetch) => getJson<TitleFlowRun>(`/api/queue/file-run/${dramaId}`, f)
export const startFileRun = (dramaId: number, body: { job_id: string; engine: string; locale: string | null }, f?: Fetch) =>
  postJson<TitleFlowRun>(`/api/queue/file-run/${dramaId}`, body, f)

// The translating that follows a chapter import: comic pages, or a novel's text, lines, names and translation.
export const startAfterImport = (dramaId: number, f?: Fetch) =>
  postJson<TitleFlowRun>(`/api/queue/after-import/${dramaId}`, undefined, f)
