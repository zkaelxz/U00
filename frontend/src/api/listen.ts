import type { ListenConfig, ListenRunRequest, ListenTestVoiceRequest } from '../types/listen'
import { apiUrl, getJson, postJson, type Fetch } from './client'

export const listenApi = {
  config: (dramaId: number, f?: Fetch) => getJson<ListenConfig>(`/api/listen/dramas/${dramaId}/config`, f),
  run: (dramaId: number, body: ListenRunRequest, f?: Fetch) =>
    postJson<{ job_id: string }>(`/api/listen/dramas/${dramaId}/run`, body, f),
  testVoice: (dramaId: number, body: ListenTestVoiceRequest, f?: Fetch) =>
    postJson<{ job_id: string }>(`/api/listen/dramas/${dramaId}/test-voice`, body, f),
}

// The last Test voice sentence (media.stream); `run` keeps the browser from replaying an older one.
export const listenTestVoiceUrl = (dramaId: number, run: number) => apiUrl(`/api/listen/dramas/${dramaId}/test-voice?run=${run}`)

// The finished narration track (media.stream).
export const listenTrackUrl = (dramaId: number) => apiUrl(`/api/listen/dramas/${dramaId}/track`)
