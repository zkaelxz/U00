// Review AI extras (/api/review-extras/...): auto-merge short lines (preview,
// then apply with the preview's ids and groups; 409 if either changed), learn
// my style (reset is PC-only), SenseVoice audio tags and the burned-subtitle
// preview clip.
import type {
  BurnPreviewInfo,
  BurnPreviewStart,
  BurnPreviewStarted,
  MergeShortApply,
  MergeShortOptions,
  MergeShortPreview,
  MergeShortResult,
  SenseVoiceStarted,
  SenseVoiceTags,
  StyleLearnRequest,
  StyleState,
} from '../types/reviewExtras'
import { apiUrl, getJson, postJson } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

const base = (id: number) => `/api/review-extras/dramas/${id}`

export function mergeShortQuery(opts: Partial<MergeShortOptions> = {}): string {
  const params = new URLSearchParams()
  for (const key of ['min_duration', 'max_gap', 'max_chars'] as const) {
    const v = opts[key]
    if (v !== undefined && v !== null && Number.isFinite(v)) params.set(key, String(v))
  }
  const qs = params.toString()
  return qs ? `?${qs}` : ''
}

export const previewMergeShort = (id: number, opts: Partial<MergeShortOptions> = {}, f?: Fetch) =>
  getJson<MergeShortPreview>(`${base(id)}/merge-short/preview${mergeShortQuery(opts)}`, f)

export const applyMergeShort = (id: number, body: MergeShortApply, f?: Fetch) =>
  postJson<MergeShortResult>(`${base(id)}/merge-short/apply`, body, f)

export const getStyle = (id: number, f?: Fetch) => getJson<StyleState>(`${base(id)}/style`, f)

export const learnStyle = (id: number, body: StyleLearnRequest = {}, f?: Fetch) => {
  // Blank engine/model are left out (the server uses the drama's defaults).
  const payload: Record<string, unknown> = {}
  if (body.engine) payload.engine = body.engine
  if (body.model) payload.model = body.model
  return postJson<StyleState>(`${base(id)}/style/learn`, payload, f)
}

export const setStyleApplied = (id: number, apply: boolean, f?: Fetch) =>
  postJson<StyleState>(`${base(id)}/style/apply`, { apply }, f)

// PC-only (it wipes a series-wide or library-wide profile): X-Baihe-Local, and a 403 marks the tab remote.
export const resetStyle = (id: number, f?: Fetch) =>
  postJson<StyleState>(`${base(id)}/style/reset`, { confirm: true }, pcOnlyFetch(f))

// PC-only: brings back an earlier profile (index 0 = the one the last learn or reset replaced).
export const restoreStyle = (id: number, index = 0, f?: Fetch) =>
  postJson<StyleState>(`${base(id)}/style/restore`, { index }, pcOnlyFetch(f))

export const startSenseVoice = (id: number, f?: Fetch) =>
  postJson<SenseVoiceStarted>(`${base(id)}/sensevoice`, undefined, f)

export const getSenseVoice = (id: number, f?: Fetch) => getJson<SenseVoiceTags>(`${base(id)}/sensevoice`, f)

export const startBurnPreview = (id: number, body: BurnPreviewStart, f?: Fetch) => {
  const payload: Record<string, unknown> = { line_id: body.line_id }
  if (body.pad_seconds !== undefined && body.pad_seconds !== null) payload.pad_seconds = body.pad_seconds
  if (body.preset) payload.preset = body.preset
  if (body.style) payload.style = body.style
  if (body.speaker_colors) payload.speaker_colors = body.speaker_colors
  if (body.per_speaker_colors !== undefined) payload.per_speaker_colors = body.per_speaker_colors
  if (body.wrap_chars_en !== undefined) payload.wrap_chars_en = body.wrap_chars_en
  if (body.wrap_chars_source !== undefined) payload.wrap_chars_source = body.wrap_chars_source
  return postJson<BurnPreviewStarted>(`${base(id)}/burn-preview`, payload, f)
}

export const getBurnPreviewInfo = (id: number, f?: Fetch) =>
  getJson<BurnPreviewInfo>(`${base(id)}/burn-preview/info`, f)

// The <video> element fetches this itself (Range); `v` busts the cache after a re-render.
export function burnPreviewClipUrl(id: number, version: string | number): string {
  return apiUrl(`${base(id)}/burn-preview/clip?v=${encodeURIComponent(String(version))}`)
}
