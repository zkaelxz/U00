import type { QuickVideoRun, QuickVideoSettings, QuickVideoStarted } from '../types/quickVideo'
import { getJson, postJson, type Fetch } from './client'
import { pcOnlyFetch } from './pcOnly'

export const getQuickVideoSettings = (f?: Fetch) => getJson<QuickVideoSettings>('/api/quick-video/settings', f)
export const setQuickVideoProfile = (profileId: number | null, f?: Fetch) =>
  postJson<QuickVideoSettings>('/api/quick-video/settings', { profile_id: profileId }, f)
// Needs Import from a link and Start jobs, from any device; the run is on the PC.
// profileId omitted = the profile chosen in Settings.
export const startQuickVideo = (url: string, watchOnce: boolean, profileId?: number, f?: Fetch) =>
  postJson<QuickVideoStarted>(
    '/api/quick-video/start',
    { url, watch_once: watchOnce, ...(profileId === undefined ? {} : { profile_id: profileId }) },
    f,
  )
export const getQuickVideoRuns = (f?: Fetch) =>
  getJson<{ items: QuickVideoRun[] }>('/api/quick-video/runs', f).then((r) => r.items)
// Removes a watch-once title and only that.
export const deleteQuickVideoTitle = (dramaId: number, f?: Fetch) =>
  postJson<{ deleted: boolean }>(`/api/quick-video/${dramaId}/delete`, { confirm: true }, pcOnlyFetch(f))
