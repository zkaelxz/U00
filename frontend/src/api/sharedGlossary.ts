import { getJson, postJson, type Fetch } from './client'

// Shared glossary groups (api/routers/shared_glossary_routes.py) and a series' effective glossary
// (GET/POST /api/series/{id}/shared-glossary): the group's terms overlaid by the series' own.
export interface SharedGlossaryGroup {
  id: number
  name: string
  term_count: number
  series_count: number
  can_edit: boolean
}

export interface EffectiveTerm {
  id: number
  term_original: string
  term_translation: string
  notes: string
  origin: 'series' | 'shared'
  origin_name: string | null
}

export interface SeriesSharedGlossary {
  series_id: number
  group: SharedGlossaryGroup | null
  terms: EffectiveTerm[]
  can_edit: boolean
}

export const listSharedGroups = (f?: Fetch) => getJson<{ items: SharedGlossaryGroup[] }>('/api/shared-glossaries', f)

export const createSharedGroup = (name: string, f?: Fetch) =>
  postJson<SharedGlossaryGroup>('/api/shared-glossaries', { name }, f)

export const getSeriesSharedGlossary = (seriesId: number, f?: Fetch) =>
  getJson<SeriesSharedGlossary>(`/api/series/${seriesId}/shared-glossary`, f)

/** Join a group, or leave it with null; the series' own terms are never changed. */
export const setSeriesSharedGroup = (seriesId: number, groupId: number | null, f?: Fetch) =>
  postJson<SeriesSharedGlossary>(`/api/series/${seriesId}/shared-glossary`, { group_id: groupId }, f)

export const saveSharedTerm = (groupId: number, term_original: string, term_translation: string, f?: Fetch) =>
  postJson<unknown>(`/api/shared-glossaries/${groupId}/terms`, { term_original, term_translation }, f)

export const deleteSharedTerm = (groupId: number, termId: number, f?: Fetch) =>
  postJson<{ deleted: boolean }>(`/api/shared-glossaries/${groupId}/terms/${termId}/delete`, undefined, f)

/** Where an effective term came from, as shown beside it. */
export const originLabel = (t: Pick<EffectiveTerm, 'origin' | 'origin_name'>): string =>
  t.origin === 'shared' ? `Shared: ${t.origin_name ?? 'group'}` : 'This series'
