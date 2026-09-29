import type {
  MediaRemoveResult,
  RawNovelRemoveResult,
  SeriesCharacter,
  SeriesCharacterDeleteResult,
  TranslationVersionDeleteResult,
} from '../types/autotuneGlossary'
import { getJson, postJson } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

const CONFIRM = { confirm: true }

// PC-only deletes (api/routers/delete_routes.py). Each needs {confirm: true}
// and answers 409 while a job for the drama runs. pcOnlyFetch adds
// X-Baihe-Local: 1 and marks the tab remote on a 403.
export const removeMedia = (dramaId: number, f?: Fetch) =>
  postJson<MediaRemoveResult>(`/api/media/dramas/${dramaId}/remove`, CONFIRM, pcOnlyFetch(f))

export const removeRawNovel = (dramaId: number, f?: Fetch) =>
  postJson<RawNovelRemoveResult>(`/api/novel/dramas/${dramaId}/raw-novel/remove`, CONFIRM, pcOnlyFetch(f))

export const deleteVersion = (dramaId: number, versionId: number, f?: Fetch) =>
  postJson<TranslationVersionDeleteResult>(
    `/api/review/dramas/${dramaId}/versions/${versionId}/delete`,
    CONFIRM,
    pcOnlyFetch(f),
  )

export const deleteSeriesCharacter = (seriesId: number, characterId: number, f?: Fetch) =>
  postJson<SeriesCharacterDeleteResult>(
    `/api/characters/series/${seriesId}/characters/${characterId}/delete`,
    CONFIRM,
    pcOnlyFetch(f),
  )

export const listSeriesCharacters = (seriesId: number, f?: Fetch) =>
  getJson<SeriesCharacter[]>(`/api/characters/series/${seriesId}/characters`, f)
