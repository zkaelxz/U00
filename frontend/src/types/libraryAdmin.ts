// Mirrors the Library* admin models (route batch 2A) and the PC-only delete
// results for presets and voice bank entries in api/schemas.py.

// services/library_admin_service.py STATUSES and db.ORGANIZATIONAL_TAGS.
export const LIBRARY_STATUSES = ['not started', 'aligned', 'translated', 'dubbed', 'exported'] as const
export const LIBRARY_LIST_TAGS = ['Favorite', 'On Hold', 'Plan to Translate'] as const
// storage.STORAGE_QUALITY_PRESETS, with its own labels.
export const STORAGE_PRESETS = [
  ['archival', 'Archival: keep everything'],
  ['balanced', 'Balanced: keep finals, drop intermediates'],
  ['minimal', 'Minimal: sources and text only'],
] as const

export type LibraryStatus = (typeof LIBRARY_STATUSES)[number]
export type LibraryListTag = (typeof LIBRARY_LIST_TAGS)[number]
export type StoragePreset = (typeof STORAGE_PRESETS)[number][0]
export type ArtifactKind = 'backup' | 'export' | 'database' | 'user_backup'

// Fixed job ids (library_admin_service.*_JOB_ID).
export const ADMIN_JOB_IDS = {
  translate: 'bulk_series_translate',
  export: 'library_export_zip',
  backup: 'library_backup',
  database: 'library_db_backup',
  userBackup: 'library_user_backup',
} as const

// error: not_found | job_running | delete_failed | not_translated
export interface LibraryBulkItem {
  drama_id: number
  ok: boolean
  error?: string | null
  message?: string | null
  warning?: string | null
  freed_bytes?: number | null
}

export interface LibraryBulkResult {
  results: LibraryBulkItem[]
  updated: number
}

export interface LibraryBulkDeleteResult {
  results: LibraryBulkItem[]
  deleted: number
}

export interface LibraryBulkTranslateSkip {
  drama_id: number
  reason: string
}

export interface LibraryBulkTranslateStarted {
  job_id: string
  queued: number[]
  skipped: LibraryBulkTranslateSkip[]
}

export interface LibraryExportStarted {
  job_id: string
  drama_ids: number[]
  results?: LibraryBulkItem[] | null
}

export interface LibraryJobStarted {
  job_id: string
}

export interface LibraryArtifactInfo {
  kind: ArtifactKind
  name: string
  size: number
}

export interface LibraryRestoreDone {
  restored: boolean
  sessions_revoked: number
}

export interface LibraryStorageCategory {
  key: string
  label: string
  note: string
  bytes: number
  selected: boolean
}

export interface LibraryStorageDrama {
  drama_id: number
  total_bytes: number
  would_free_bytes: number
  job_running: boolean
}

export interface LibraryStorageScan {
  preset: string
  categories_to_clean: string[]
  total_bytes: number
  reclaimable_bytes: number
  would_free_bytes: number
  categories: LibraryStorageCategory[]
  per_drama: LibraryStorageDrama[]
}

export interface LibraryStorageCleanResult {
  preset: string
  freed_bytes: number
  results: LibraryBulkItem[]
}

export interface PresetDeleteResult {
  preset_id: number
  deleted: boolean
}

export interface VoiceBankDeleteResult {
  entry_id: number
  deleted: boolean
}
