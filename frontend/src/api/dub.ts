import type {
  DubConfig,
  DubPacing,
  DubRunRequest,
  NarrationConfig,
  NarrationRunRequest,
} from '../types/dub'
import { apiUrl, getJson, postJson } from './client'

type Fetch = typeof fetch

export const dubApi = {
  config: (dramaId: number, f?: Fetch) => getJson<DubConfig>(`/api/dub/dramas/${dramaId}/config`, f),
  pacing: (dramaId: number, f?: Fetch) => getJson<DubPacing>(`/api/dub/dramas/${dramaId}/pacing`, f),
  run: (dramaId: number, body: DubRunRequest, f?: Fetch) =>
    postJson<{ job_id: string }>(`/api/dub/dramas/${dramaId}/run`, body, f),
}

// Plain link (Content-Disposition: attachment).
export const dubTrackUrl = (dramaId: number) => apiUrl(`/api/dub/dramas/${dramaId}/track`)

export const narrationApi = {
  config: (dramaId: number, f?: Fetch) =>
    getJson<NarrationConfig>(`/api/narration/dramas/${dramaId}/config`, f),
  run: (dramaId: number, body: NarrationRunRequest, f?: Fetch) =>
    postJson<{ job_id: string }>(`/api/narration/dramas/${dramaId}/run`, body, f),
}
