import { getJson, postJson } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

// The Series page (api/routers/series_routes.py): list, page, rename,
// delete (PC only) and copy glossary terms in from another series.
export interface SeriesListItem {
  id: number
  name: string
  is_private: boolean
  owned_by_me: boolean
  title_count: number
  glossary_term_count: number
}

export interface SeriesTitle {
  id: number
  title_en: string | null
  title_zh: string | null
  media_type: string | null
  status: string | null
  episode_number: number | null
  line_count: number
}

export interface SeriesPageData {
  id: number
  name: string
  is_private: boolean
  owned_by_me: boolean
  can_edit: boolean
  has_instructions: boolean
  glossary_term_count: number
  character_count: number
  titles: SeriesTitle[]
}

export interface SeriesDeletePreview {
  series_id: number
  name: string
  title_count: number
  glossary_term_count: number
  character_count: number
  translation_memory_count: number
  dismissal_count: number
  style_profile_count: number
  has_instructions: boolean
  needs_typed_confirm: boolean
  job_running: boolean
}

export interface SeriesDeleteResult {
  deleted: boolean
  series_id: number
  titles_kept: number
  glossary_terms_deleted: number
  characters_deleted: number
}

export interface SeriesCopyTermsPreview {
  series_id: number
  source_series_id: number
  new_count: number
  skipped_count: number
  new_terms: string[]
  skipped_terms: string[]
}

export interface SeriesCopyTermsResult {
  series_id: number
  source_series_id: number
  added: number
  skipped: number
}

export const listAllSeries = (f?: Fetch) => getJson<{ items: SeriesListItem[] }>('/api/series', f)

export const getSeriesPage = (id: number, f?: Fetch) => getJson<SeriesPageData>(`/api/series/${id}`, f)

export const renameSeries = (id: number, name: string, f?: Fetch) =>
  postJson<{ id: number; name: string }>(`/api/series/${id}/rename`, { name }, f)

export const getSeriesDeletePreview = (id: number, f?: Fetch) =>
  getJson<SeriesDeletePreview>(`/api/series/${id}/delete-preview`, f)

// The API refuses a series that holds anything unless confirm_text is DELETE;
// the typed word in the UI is the same gate on this side.
export const deleteSeries = (id: number, typed: boolean, f?: Fetch) =>
  postJson<SeriesDeleteResult>(
    `/api/series/${id}/delete`,
    typed ? { confirm: true, confirm_text: 'DELETE' } : { confirm: true },
    pcOnlyFetch(f),
  )

export const getCopyTermsPreview = (id: number, sourceId: number, f?: Fetch) =>
  getJson<SeriesCopyTermsPreview>(`/api/series/${id}/copy-terms-preview?source_series_id=${sourceId}`, f)

export const copyTerms = (id: number, sourceId: number, f?: Fetch) =>
  postJson<SeriesCopyTermsResult>(`/api/series/${id}/copy-terms`, { source_series_id: sourceId }, f)
