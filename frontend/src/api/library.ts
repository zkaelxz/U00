import type { DramaDetail } from './types'
import type {
  DramaCreateRequest,
  DramaCreateResult,
  SameTitle,
  SameTitleQuery,
  DramaDeleteResult,
  DramaMetadataUpdate,
  LibraryContinueResponse,
  LibraryCostResponse,
  LibraryDashboard,
  LibraryFilterOptions,
  LibraryHistoryResponse,
  LibraryPreset,
  LibraryPresetsResponse,
  LibraryRecentResponse,
  LibrarySearchResponse,
  LibrarySeriesResponse,
  LibraryVoice,
  LibraryVoiceBankResponse,
  ReadingHistoryClearResult,
  LibraryHome,
  LibraryRecap,
} from '../types/library'
import { deleteJson, getJson, postJson, postMultipart, type Fetch } from './client'
import { pcOnlyFetch } from './pcOnly'

export const getStats = (f?: Fetch) => getJson<LibraryDashboard>('/api/library/stats', f)
export const getRecent = (f?: Fetch) => getJson<LibraryRecentResponse>('/api/library/recent', f)
export const getCosts = (f?: Fetch) => getJson<LibraryCostResponse>('/api/library/costs', f)
// Every series with a title, one-title ones too (without include_single the API lists only those with two or more).
export const getAllSeries = (f?: Fetch) =>
  getJson<LibrarySeriesResponse>('/api/library/series?include_single=true', f)
export const getHistory = (f?: Fetch) => getJson<LibraryHistoryResponse>('/api/library/history', f)
export const getHome = (f?: Fetch) => getJson<LibraryHome>('/api/library/home', f)
export const getRecap = (month: string, f?: Fetch) =>
  getJson<LibraryRecap>(`/api/library/recap?month=${encodeURIComponent(month)}`, f)
export const getContinueReading = (f?: Fetch) =>
  getJson<LibraryContinueResponse>('/api/library/continue', f)
export const getFilterOptions = (f?: Fetch) =>
  getJson<LibraryFilterOptions>('/api/library/filter-options', f)
// PC only; reading progress (the Continue strip) is kept.
export const clearReadingHistory = (f?: Fetch) =>
  postJson<ReadingHistoryClearResult>('/api/library/history/clear', { confirm: true }, pcOnlyFetch(f))
export const getPresets = (f?: Fetch) => getJson<LibraryPresetsResponse>('/api/library/presets', f)
export const getVoiceBank = (f?: Fetch) =>
  getJson<LibraryVoiceBankResponse>('/api/library/voice-bank', f)
// Parity L18/L19: rename only (a duplicate preset name is a 409).
export const renamePreset = (id: number, name: string, f?: Fetch) =>
  postJson<LibraryPreset>(`/api/library/presets/${id}/rename`, { name }, f)
export interface NewVoice {
  name: string
  file: File
  ref_text?: string
  clone_engine?: string
  language?: string
  notes?: string
}
export function addVoiceBankEntry(v: NewVoice, f?: Fetch) {
  const form = new FormData()
  form.append('name', v.name)
  form.append('file', v.file, v.file.name)
  for (const k of ['ref_text', 'clone_engine', 'language', 'notes'] as const) {
    const val = v[k]?.trim()
    if (val) form.append(k, val)
  }
  return postMultipart<LibraryVoice>('/api/library/voice-bank', form, f)
}
export const editVoiceBankDetails = (id: number, d: { notes: string; ref_text: string; language: string }, f?: Fetch) =>
  postJson<LibraryVoice>(`/api/library/voice-bank/${id}/details`, d, f)
export function replaceVoiceBankClip(id: number, file: File, f?: Fetch) {
  const form = new FormData()
  form.append('file', file, file.name)
  return postMultipart<LibraryVoice>(`/api/library/voice-bank/${id}/clip`, form, f)
}
export const setVoiceBankPrivate = (id: number, priv: boolean, f?: Fetch) =>
  postJson<LibraryVoice>(`/api/library/voice-bank/${id}/private`, { private: priv }, f)
export const renameVoiceBankEntry = (id: number, name: string, f?: Fetch) =>
  postJson<LibraryVoice>(`/api/library/voice-bank/${id}/rename`, { name }, f)
export const searchLines =(q: string, f?: Fetch) =>
  getJson<LibrarySearchResponse>(`/api/library/search?q=${encodeURIComponent(q)}`, f)

export const createDrama = (body: DramaCreateRequest, f?: Fetch) =>
  postJson<DramaCreateResult>('/api/dramas', body, f)

export const sameTitles = (body: SameTitleQuery, f?: Fetch) =>
  postJson<{ items: SameTitle[] }>('/api/dramas/same', body, f)

/** For each source page link, the id of a title the caller can see that carries it, else null; same order, links not echoed. */
export const titlesByLinks = (urls: string[], f?: Fetch) =>
  postJson<{ title_ids: (number | null)[] }>('/api/dramas/by-links', { urls }, f)

// Partial update: send only the fields the user changed.
export const updateDramaMetadata = (id: number, body: DramaMetadataUpdate, f?: Fetch) =>
  postJson<DramaDetail>(`/api/dramas/${id}/metadata`, body, f)

// Series only, so an admin can use it from away (library.create); the metadata
// update above needs admin.library, which the remote listener drops.
export const setDramaSeries = (id: number, body: Pick<DramaMetadataUpdate, 'series_id' | 'new_series_name'>, f?: Fetch) =>
  postJson<DramaDetail>(`/api/dramas/${id}/series`, body, f)

// Makes an independent test copy of a title (library.create, so it works from away).
export const duplicateDrama = (id: number, f?: Fetch) =>
  postJson<DramaDetail>(`/api/dramas/${id}/duplicate`, {}, f)

// PC only: the source link as imported or typed, query included (the
// drama detail's source_url is the cleaned form).
export const getSourceLink = (id: number, f?: Fetch) =>
  getJson<{ url: string }>(`/api/dramas/${id}/source-link`, f)

// PC only: opens the title's folder in the PC's file manager.
export const openDramaFolder = (id: number, f?: Fetch) =>
  postJson<{ opened: boolean; folder?: string | null }>(`/api/dramas/${id}/folder/open`, {}, pcOnlyFetch(f))

// The API refuses unless both confirmation params match; the typed-word
// check in the UI is a second gate on top of that.
export const deleteDrama = (id: number, f?: Fetch) =>
  deleteJson<DramaDeleteResult>(`/api/dramas/${id}?confirm=true&confirm_text=DELETE`, pcOnlyFetch(f))

// A title the signed-in person owns, from any device; its 403 means "not yours", not "not the PC".
export const deleteOwnDrama = (id: number, f?: Fetch) =>
  postJson<DramaDeleteResult>(`/api/dramas/${id}/delete-mine`, { confirm: true, confirm_text: 'DELETE' }, f)

export type CaptureNote = { summary: string; created_at: string }
export const getCaptureNotes = (id: number, f?: Fetch) =>
  getJson<{ items: CaptureNote[] }>(`/api/library/dramas/${id}/capture-notes`, f)
