// Saved manga (api/routers/saved_comics_routes.py). The folder routes are PC
// only, so they go through pcOnlyFetch (a 403 marks the tab remote); listing
// needs library.read and the page images media.stream.
import type {
  SavedChapterList, SavedChapterPages, SavedComicsFolder, SavedSeries,
} from '../types/savedComics'
import { apiUrl, getJson, postJson } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

const BASE = '/api/saved-comics'

const query = (q: Record<string, string | number>) =>
  new URLSearchParams(Object.entries(q).map(([k, v]) => [k, String(v)])).toString()

export const getSaveFolder = (f?: Fetch) => getJson<SavedComicsFolder>(`${BASE}/folder`, pcOnlyFetch(f))

// "" goes back to the default folder.
export const setSaveFolder = (folder: string, f?: Fetch) =>
  postJson<SavedComicsFolder>(`${BASE}/folder`, { folder }, pcOnlyFetch(f))

export const openSaveFolder = (f?: Fetch) => postJson<{ opened: boolean }>(`${BASE}/folder/open`, {}, pcOnlyFetch(f))

export const listSavedSeries = (f?: Fetch) => getJson<SavedSeries[]>(`${BASE}/series`, f)

export const listSavedChapters = (source: string, series: string, f?: Fetch) =>
  getJson<SavedChapterList>(`${BASE}/chapters?${query({ source, series })}`, f)

export const getSavedPages = (source: string, series: string, chapter: string, f?: Fetch) =>
  getJson<SavedChapterPages>(`${BASE}/pages?${query({ source, series, chapter })}`, f)

// The <img> src for one page (1-based).
export const savedPageUrl = (source: string, series: string, chapter: string, page: number) =>
  apiUrl(`${BASE}/page?${query({ source, series, chapter, page })}`)
