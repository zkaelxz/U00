// Mirrors the Library* admin models and the PC-only delete
// results for presets and voice bank entries in api/schemas/library.py.
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

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

// error: not_found | job_running | upload_running | delete_failed | not_translated
export type LibraryBulkItem = Schemas['LibraryBulkItem']

export type LibraryBulkResult = Schemas['LibraryBulkResult']

export type LibraryBulkDeleteResult = Schemas['LibraryBulkDeleteResult']

export type LibraryBulkTranslateSkip = Schemas['LibraryBulkTranslateSkip']

export type LibraryBulkTranslateStarted = Schemas['LibraryBulkTranslateStarted']

export type LibraryExportStarted = Schemas['LibraryExportStarted']

export type LibraryJobStarted = Schemas['LibraryJobStarted']

export type LibraryArtifactInfo = Schemas['LibraryArtifactInfo']

export type LibraryRestoreDone = Schemas['LibraryRestoreDone']

export type LibraryStorageScan = Schemas['LibraryStorageScan']

export type LibraryStorageCleanResult = Schemas['LibraryStorageCleanResult']

export type PresetDeleteResult = Schemas['PresetDeleteResult']

export type VoiceBankDeleteResult = Schemas['VoiceBankDeleteResult']
