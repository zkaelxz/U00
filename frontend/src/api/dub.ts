import type { NarrationConfig, NarrationRunRequest } from '../types/dub'
import type { NarrationEstimate } from '../types/dub'
import { getJson, postJson, type Fetch } from './client'

export interface NarrationEstimateParams {
  engine?: string | null
  model?: string | null
  chapter_from?: number | null
  chapter_to?: number | null
}

export const narrationApi = {
  estimate: (dramaId: number, p: NarrationEstimateParams, f?: Fetch) => {
    const q = new URLSearchParams()
    for (const [k, v] of Object.entries(p)) if (v !== undefined && v !== null && v !== '') q.set(k, String(v))
    const qs = q.toString()
    return getJson<NarrationEstimate>(`/api/narration/dramas/${dramaId}/estimate${qs ? `?${qs}` : ''}`, f)
  },
  config: (dramaId: number, f?: Fetch) =>
    getJson<NarrationConfig>(`/api/narration/dramas/${dramaId}/config`, f),
  // fresh: drop the batches an interrupted run already tagged and tag
  // everything again; sent only when true.
  run: (dramaId: number, body: NarrationRunRequest, f?: Fetch, opts: { fresh?: boolean } = {}) =>
    postJson<{ job_id: string }>(
      `/api/narration/dramas/${dramaId}/run${opts.fresh ? '?fresh=true' : ''}`,
      body,
      f,
    ),
}
