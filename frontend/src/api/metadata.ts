import type { DramaDetail } from './types'
import { postJson } from './client'
import type { AutofillRequest, AutofillSuggestion, MediaAnalysis } from '../types/workspace'

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
