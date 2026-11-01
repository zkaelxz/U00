import type { ListenConfig, ListenRunRequest } from '../types/listen'
import { apiUrl, getJson, postJson, type Fetch } from './client'

export const listenApi = {
  config: (dramaId: number, f?: Fetch) => getJson<ListenConfig>(`/api/listen/dramas/${dramaId}/config`, f),
  run: (dramaId: number, body: ListenRunRequest, f?: Fetch) =>
    postJson<{ job_id: string }>(`/api/listen/dramas/${dramaId}/run`, body, f),
}

// The finished narration track (media.stream).
export const listenTrackUrl = (dramaId: number) => apiUrl(`/api/listen/dramas/${dramaId}/track`)
