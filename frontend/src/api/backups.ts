// Automatic backups and single-drama restore (api/routers/backup_routes.py,
// roadmap Step 43). Every route is PC only, so every call, reads included,
// goes through pcOnlyFetch: X-Baihe-Local, CSRF when signed in, and a 403
// marks the tab remote so the cards switch to "Run this on the main PC."
import type {
  AutoBackupSettings,
  AutoBackupSettingsUpdate,
  BackupJobStarted,
  DeleteSnapshotDone,
  RestoreDramaDone,
  SnapshotDramaList,
  SnapshotInfo,
} from '../types/backups'
import { DELETE_SNAPSHOT_WORD, RESTORE_SNAPSHOT_WORD } from '../types/backups'
import { getJson, postJson } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

const BASE = '/api/backups'

export const getBackupSettings = (f?: Fetch) =>
  getJson<AutoBackupSettings>(`${BASE}/settings`, pcOnlyFetch(f))

/** Send only the fields that changed; the rest stay as they are. */
export const updateBackupSettings = (changes: AutoBackupSettingsUpdate, f?: Fetch) =>
  postJson<AutoBackupSettings>(`${BASE}/settings`, changes, pcOnlyFetch(f))

/** replace must be true when a snapshot exists (the new backup replaces it). */
export const backUpNow = (replace: boolean, f?: Fetch) =>
  postJson<BackupJobStarted>(`${BASE}/now`, { replace }, pcOnlyFetch(f))

export const getSnapshot = (f?: Fetch) => getJson<SnapshotInfo>(`${BASE}/snapshot`, pcOnlyFetch(f))

export const getSnapshotDramas = (f?: Fetch) =>
  getJson<SnapshotDramaList>(`${BASE}/snapshot/dramas`, pcOnlyFetch(f))

/** drama_id is the drama's id inside the snapshot. */
export const restoreSnapshotDrama = (drama_id: number, f?: Fetch) =>
  postJson<RestoreDramaDone>(
    `${BASE}/snapshot/restore-drama`,
    { drama_id, confirm: true, confirm_text: RESTORE_SNAPSHOT_WORD },
    pcOnlyFetch(f),
  )

export const deleteSnapshot = (f?: Fetch) =>
  postJson<DeleteSnapshotDone>(
    `${BASE}/snapshot/delete`, { confirm: true, confirm_text: DELETE_SNAPSHOT_WORD }, pcOnlyFetch(f),
  )
