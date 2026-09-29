import type { DramaDetail } from './types'
import type {
  DramaCreateRequest,
  DramaCreateResult,
  DramaDeleteResult,
  DramaMetadataUpdate,
  LibraryContinueResponse,
  LibraryCostResponse,
  LibraryDashboard,
  LibraryFilterOptions,
  LibraryHistoryResponse,
  LibraryPresetsResponse,
  LibraryRecentResponse,
  LibrarySearchResponse,
  LibrarySeriesResponse,
  LibraryVoiceBankResponse,
  ReadingHistoryClearResult,
} from '../types/library'
import { deleteJson, getJson, postJson } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

export const getStats = (f?: Fetch) => getJson<LibraryDashboard>('/api/library/stats', f)
export const getRecent = (f?: Fetch) => getJson<LibraryRecentResponse>('/api/library/recent', f)
export const getCosts = (f?: Fetch) => getJson<LibraryCostResponse>('/api/library/costs', f)
export const getSeries = (f?: Fetch) => getJson<LibrarySeriesResponse>('/api/library/series', f)
export const getHistory = (f?: Fetch) => getJson<LibraryHistoryResponse>('/api/library/history', f)
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
export const searchLines = (q: string, f?: Fetch) =>
  getJson<LibrarySearchResponse>(`/api/library/search?q=${encodeURIComponent(q)}`, f)

export const createDrama = (body: DramaCreateRequest, f?: Fetch) =>
  postJson<DramaCreateResult>('/api/dramas', body, f)

// Partial update: send only the fields the user changed.
export const updateDramaMetadata = (id: number, body: DramaMetadataUpdate, f?: Fetch) =>
  postJson<DramaDetail>(`/api/dramas/${id}/metadata`, body, f)

// The API refuses unless both confirmation params match; the typed-word
// check in the UI is a second gate on top of that.
export const deleteDrama = (id: number, f?: Fetch) =>
  deleteJson<DramaDeleteResult>(`/api/dramas/${id}?confirm=true&confirm_text=DELETE`, pcOnlyFetch(f))
