import type { NarrationConfig, NarrationRunRequest } from '../types/dub'
import { getJson, postJson, type Fetch } from './client'

export const narrationApi = {
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
