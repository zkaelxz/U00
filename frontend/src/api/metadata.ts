import type { DramaDetail } from './types'
import { apiUrl, getJson, postJson, postMultipart } from './client'
import { pcOnlyFetch } from './pcOnly'
import type {
  AutofillRequest, AutofillSuggestion, CoverArtResult, KnownPlatform, MediaAnalysis, RomanizeCreditsResult,
} from '../types/workspace'

type Fetch = typeof fetch

const base = (id: number) => `/api/metadata/dramas/${id}`

export const analyzeMedia = (id: number, f: Fetch = fetch) =>
  postJson<MediaAnalysis>(`${base(id)}/analyze-media`, undefined, f)

// Writes nothing: returns suggested values only.
export const suggestMetadata = (id: number, req: AutofillRequest, f: Fetch = fetch) =>
  postJson<AutofillSuggestion>(`${base(id)}/autofill`, req, f)

// `fields` must hold only the values the user accepted.
export const applyMetadata = (id: number, fields: Record<string, string>, f: Fetch = fetch) =>
  postJson<DramaDetail>(`${base(id)}/autofill/apply`, fields, f)

// Writes only the *_romanized credit fields; `engine` defaults to the drama's.
export const romanizeCredits = (id: number, engine?: string, f: Fetch = fetch) =>
  postJson<RomanizeCreditsResult>(`${base(id)}/romanize-credits`, engine ? { engine } : {}, f)

// Known official platforms for the auto-fill box (read-only list).
export const listPlatforms = (f: Fetch = fetch) =>
  getJson<{ platforms: KnownPlatform[] }>('/api/discover/platforms', f).then((r) => r.platforms)

// Cover art: upload is PC only (checked and re-encoded server side).
export function uploadCover(id: number, file: File, f?: Fetch) {
  const form = new FormData()
  form.append('file', file)
  return postMultipart<CoverArtResult>(`/api/dramas/${id}/cover`, form, pcOnlyFetch(f))
}

// An <img src>; `v` busts the browser cache after a new upload.
export const coverUrl = (id: number, v: number | string = 0) =>
  apiUrl(`/api/dramas/${id}/cover${v ? `?v=${encodeURIComponent(String(v))}` : ''}`)
