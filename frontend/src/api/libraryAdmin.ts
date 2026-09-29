// Library admin (route batch 2A, /api/library/admin) and the Library's
// PC-only deletes (presets, voice bank). PC-only calls go through
// pcOnlyFetch (X-Baihe-Local header; a 403 marks the tab remote).
import type {
  ArtifactKind,
  LibraryArtifactInfo,
  LibraryBulkDeleteResult,
  LibraryBulkResult,
  LibraryBulkTranslateStarted,
  LibraryExportStarted,
  LibraryJobStarted,
  LibraryListTag,
  LibraryRestoreDone,
  LibraryStatus,
  LibraryStorageCleanResult,
  LibraryStorageScan,
  PresetDeleteResult,
  StoragePreset,
  VoiceBankDeleteResult,
} from '../types/libraryAdmin'
import { apiUrl, getJson, postJson, postMultipart } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

const BASE = '/api/library/admin'

// Confirm words the server checks (library_admin_service *_CONFIRM_TEXT).
export const DELETE_WORD = 'DELETE'
export const RESTORE_WORD = 'RESTORE'
export const CLEAN_WORD = 'CLEAN'

export const bulkSetStatus = (drama_ids: number[], status: LibraryStatus, f?: Fetch) =>
  postJson<LibraryBulkResult>(`${BASE}/bulk/status`, { drama_ids, status }, f)

export const bulkSetTag = (drama_ids: number[], tag: LibraryListTag, present: boolean, f?: Fetch) =>
  postJson<LibraryBulkResult>(`${BASE}/bulk/tags`, { drama_ids, tag, present }, f)

export const bulkTranslate = (drama_ids: number[], f?: Fetch) =>
  postJson<LibraryBulkTranslateStarted>(`${BASE}/bulk/translate`, { drama_ids }, f)

export const bulkDelete = (drama_ids: number[], f?: Fetch) =>
  postJson<LibraryBulkDeleteResult>(
    `${BASE}/bulk/delete`, { drama_ids, confirm: true, confirm_text: DELETE_WORD }, pcOnlyFetch(f),
  )

// No ids: every translated/dubbed/exported drama.
export const startExport = (drama_ids?: number[], f?: Fetch) =>
  postJson<LibraryExportStarted>(`${BASE}/export`, drama_ids ? { drama_ids } : {}, pcOnlyFetch(f))

export const startBackup = (database_only: boolean, f?: Fetch) =>
  postJson<LibraryJobStarted>(`${BASE}/backup`, { database_only }, pcOnlyFetch(f))

export const getArtifactInfo = (kind: ArtifactKind, f?: Fetch) =>
  getJson<LibraryArtifactInfo>(`${BASE}/artifacts/${kind}/info`, f)

// A plain download link (PC-only GET; the browser streams it).
export const artifactDownloadUrl = (kind: ArtifactKind) => apiUrl(`${BASE}/artifacts/${kind}`)

export function restoreBackup(file: File | Blob, f?: Fetch) {
  const form = new FormData()
  form.append('file', file)
  form.append('confirm', 'true')
  form.append('confirm_text', RESTORE_WORD)
  return postMultipart<LibraryRestoreDone>(`${BASE}/restore`, form, pcOnlyFetch(f))
}

export const scanStorage = (preset: StoragePreset, f?: Fetch) =>
  getJson<LibraryStorageScan>(`${BASE}/storage?preset=${encodeURIComponent(preset)}`, f)

export const cleanStorage = (preset: StoragePreset, f?: Fetch) =>
  postJson<LibraryStorageCleanResult>(
    `${BASE}/storage/clean`, { preset, confirm: true, confirm_text: CLEAN_WORD }, pcOnlyFetch(f),
  )

export const deletePreset = (id: number, f?: Fetch) =>
  postJson<PresetDeleteResult>(`/api/library/presets/${id}/delete`, { confirm: true }, pcOnlyFetch(f))

export const deleteVoiceBankEntry = (id: number, f?: Fetch) =>
  postJson<VoiceBankDeleteResult>(`/api/library/voice-bank/${id}/delete`, { confirm: true }, pcOnlyFetch(f))
