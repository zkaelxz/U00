// Models for /api/backups, taken from the generated schema (api/backup_schemas.py).
// Every /api/backups route is PC only.
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

export type BackupFrequency = 'daily' | 'weekly' | 'monthly'
export type SnapshotKind = 'db-only' | 'full'

// services/auto_backup_service.JOB_ID ("Back up now" and the scheduled run).
export const AUTO_BACKUP_JOB_ID = 'library_auto_backup'

// The words the server checks on restore and delete.
export const RESTORE_SNAPSHOT_WORD = 'RESTORE'
export const DELETE_SNAPSHOT_WORD = 'DELETE'

export type AutoBackupSettings = Schemas['AutoBackupSettings']
export type AutoBackupSettingsUpdate = Partial<Pick<AutoBackupSettings, 'enabled' | 'frequency' | 'include_media' | 'folder'>>
export type BackupJobStarted = Schemas['BackupJobStarted']

// One of the rotating copies automatic backups keep (2 daily + 2 weekly).
// The fields an older server doesn't send stay optional: the page treats a copy with no
// `managed` as managed, which the generated type (always present) would not let it say.
export type SnapshotCopy = Omit<Schemas['SnapshotCopy'], 'managed' | 'sources_left_out'> &
  Partial<Pick<Schemas['SnapshotCopy'], 'managed' | 'sources_left_out'>>

// One copy offered by a 409 "choose_copy" answer (details.candidates).
export interface CopyCandidate {
  name: string
  created_at: string
  sequence: number | null
  size: number
  managed: boolean
}

export type SnapshotInfo = Schemas['SnapshotInfo']
export type SnapshotDrama = Schemas['SnapshotDrama']
export type SnapshotDramaList = Schemas['SnapshotDramaList']
export type RestoreDramaDone = Omit<Schemas['RestoreDramaDone'], 'owners_reassigned' | 'copy_has_accounts'> &
  Partial<Pick<Schemas['RestoreDramaDone'], 'owners_reassigned' | 'copy_has_accounts'>>
export type DeleteSnapshotDone = Schemas['DeleteSnapshotDone']

// Importing dramas from a backup file (POST /api/backups/import/list, /import).
export type BackupFileDrama = Schemas['BackupFileDrama']
export type BackupFileDramaList = Schemas['BackupFileDramaList']
export type ImportDramasDone = Schemas['ImportDramasDone']
