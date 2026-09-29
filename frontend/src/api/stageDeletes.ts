import type {
  MediaRemoveResult,
  RawNovelRemoveResult,
  SeriesCharacter,
  SeriesCharacterDeleteResult,
  TranslationVersionDeleteResult,
} from '../types/autotuneGlossary'
import { getJson, postJson } from './client'

type Fetch = typeof fetch

// Every PC-only (local_only) mutating request carries X-Baihe-Local: 1
// (required once #372 lands). client.ts is a shared file, so the header is
// added by wrapping the fetch it is given rather than by changing postJson.
export function withLocalHeader(f: Fetch = fetch): Fetch {
  return ((input: RequestInfo | URL, init: RequestInit = {}) => {
    const headers = new Headers(init.headers)
    headers.set('X-Baihe-Local', '1')
    return f(input, { ...init, headers })
  }) as Fetch
}

const CONFIRM = { confirm: true }

// PC-only deletes (api/routers/delete_routes.py). Each needs {confirm: true}
// and answers 409 while a job for the drama runs.
export const removeMedia = (dramaId: number, f?: Fetch) =>
  postJson<MediaRemoveResult>(`/api/media/dramas/${dramaId}/remove`, CONFIRM, withLocalHeader(f))

export const removeRawNovel = (dramaId: number, f?: Fetch) =>
  postJson<RawNovelRemoveResult>(`/api/novel/dramas/${dramaId}/raw-novel/remove`, CONFIRM, withLocalHeader(f))

export const deleteVersion = (dramaId: number, versionId: number, f?: Fetch) =>
  postJson<TranslationVersionDeleteResult>(
    `/api/review/dramas/${dramaId}/versions/${versionId}/delete`,
    CONFIRM,
    withLocalHeader(f),
  )

export const deleteSeriesCharacter = (seriesId: number, characterId: number, f?: Fetch) =>
  postJson<SeriesCharacterDeleteResult>(
    `/api/characters/series/${seriesId}/characters/${characterId}/delete`,
    CONFIRM,
    withLocalHeader(f),
  )

export const listSeriesCharacters = (seriesId: number, f?: Fetch) =>
  getJson<SeriesCharacter[]>(`/api/characters/series/${seriesId}/characters`, f)
