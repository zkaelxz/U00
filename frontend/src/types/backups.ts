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
  // Every copy, newest first.
  copies?: SnapshotCopy[]
}

export type AutoBackupSettingsUpdate = Partial<Pick<AutoBackupSettings, 'enabled' | 'frequency' | 'include_media' | 'folder'>>

export interface BackupJobStarted {
  job_id: string
}

// One of the rotating copies automatic backups keep (2 daily + 2 weekly).
// Named by file name only; the server matches a name sent back against its
// own listing.
export interface SnapshotCopy {
  name: string
  created_at: string | null
  size: number
  kind: SnapshotKind | null
  drama_count: number | null
  readable: boolean
  // Which rotation slot keeps it; null for a copy that can't be read.
  kept_as: 'daily' | 'weekly' | null
  // Made by this library (or, in its own default folder, before copies were
  // tagged). Only managed copies are rotated or removed by "delete all";
  // the others (another library's in a shared folder, unreadable ones) stay
  // until deleted by name.
  managed?: boolean
  // This library's copy number; the highest is the newest.
  sequence?: number | null
}

// One copy offered by a 409 "choose_copy" answer (details.candidates).
export interface CopyCandidate {
  name: string
  created_at: string
  sequence: number | null
  size: number
  managed: boolean
}

// The default copy's facts, plus every copy.
export interface SnapshotInfo {
  exists: boolean
  readable?: boolean | null
  // true = the newest copy can't be told for sure; a restore must name one.
  choose_copy?: boolean | null
  // The copy a restore uses when none is named.
  default_copy?: string | null
  created_at?: string | null
  kind?: SnapshotKind | null
  size?: number | null
  app_version?: string | null
  drama_count?: number | null
  copies?: SnapshotCopy[]
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
  // The copy these dramas are from.
  name: string
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
  // The name of the copy the drama came from.
  snapshot: string
  snapshot_kind: SnapshotKind
  // linked: back in its series; recreated: the series was gone and came back
  // from the snapshot; dropped_private: the series is now someone else's
  // private series, so the drama came back with no series.
  series: 'none' | 'linked' | 'recreated' | 'dropped_private'
  counts: Record<string, number>
  skipped_tables: string[]
}

export interface DeleteSnapshotDone {
  deleted: boolean
  count: number
  // Unmanaged copies "delete all" left in place.
  kept_unmanaged?: number
}

// Importing dramas from a backup file (POST /api/backups/import/list, /import).
export interface BackupFileDrama {
  // The drama's id inside the file.
  id: number
  title: string
  media_type: string
  line_count: number
  has_media: boolean
}

export interface BackupFileDramaList {
  kind: 'zip' | 'database'
  media_available: boolean
  // The file has columns this version doesn't know; they are ignored.
  schema_differs: boolean
  dramas: BackupFileDrama[]
}

export interface ImportedDrama {
  source_id: number
  drama_id: number
  title: string
  media_imported: boolean
}

export interface ImportDramasDone {
  imported: ImportedDrama[]
  series_created: number
  media_imported: number
  counts: Record<string, number>
}
