import type { SeriesCharacter } from '../types/autotuneGlossary'
import { postJson } from './client'

type Fetch = typeof fetch

// Add and edit a series' people (api/routers/series_people_routes.py,
// lines.edit). A person is addressed by id; delete stays in stageDeletes.
export interface SeriesPersonCreate {
  character_name: string
  pronouns?: string
  aliases?: string
  notes?: string
}

/** Omitted = leave alone, "" = clear (the name can't be blank). */
export type SeriesPersonUpdate = Partial<SeriesPersonCreate>

export const addSeriesPerson = (seriesId: number, body: SeriesPersonCreate, f?: Fetch) =>
  postJson<SeriesCharacter>(`/api/characters/series/${seriesId}/characters`, body, f)

export const updateSeriesPerson = (seriesId: number, characterId: number, body: SeriesPersonUpdate, f?: Fetch) =>
  postJson<SeriesCharacter>(`/api/characters/series/${seriesId}/characters/${characterId}`, body, f)
