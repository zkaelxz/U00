// Mirrors api/backup_schemas.py (automatic backups, roadmap Step 43).
// Every /api/backups route is PC only.

export type BackupFrequency = 'daily' | 'weekly' | 'monthly'
export type SnapshotKind = 'db-only' | 'full'

// services/auto_backup_service.JOB_ID ("Back up now" and the scheduled run).
export const AUTO_BACKUP_JOB_ID = 'library_auto_backup'

// The words the server checks on restore and delete.
export const RESTORE_SNAPSHOT_WORD = 'RESTORE'
export const DELETE_SNAPSHOT_WORD = 'DELETE'

export interface AutoBackupSettings {
  enabled: boolean
  frequency: BackupFrequency
  include_media: boolean
  // "" = the library's own backups folder.
  folder: string
  frequencies: string[]
  // ISO timestamps in UTC.
  last_run_at: string | null
  last_attempt_at: string | null
  last_error: string | null
  next_run_at: string | null
  running: boolean
}

export type AutoBackupSettingsUpdate = Partial<Pick<AutoBackupSettings, 'enabled' | 'frequency' | 'include_media' | 'folder'>>

export interface BackupJobStarted {
  job_id: string
}

export interface SnapshotInfo {
  exists: boolean
  readable?: boolean | null
  created_at?: string | null
  kind?: SnapshotKind | null
  size?: number | null
  app_version?: string | null
  drama_count?: number | null
}

export interface SnapshotDrama {
  // The drama's id inside the snapshot.
  id: number
  title: string
  media_type: string
  line_count: number
  // That id is in use now, so a restore makes a new drama "… (restored <date>)".
  exists_now: boolean
}

export interface SnapshotDramaList {
  created_at: string | null
  kind: SnapshotKind
  dramas: SnapshotDrama[]
}

export interface RestoreDramaDone {
  // The drama's id in the library after the restore.
  drama_id: number
  restored_as_new: boolean
  title: string
  media_restored: boolean
  snapshot_kind: SnapshotKind
  counts: Record<string, number>
  skipped_tables: string[]
}

export interface DeleteSnapshotDone {
  deleted: boolean
}
