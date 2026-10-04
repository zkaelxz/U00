// Automatic backups and single-drama restore (api/routers/backup_routes.py).
// Every route is PC only, so every call, reads included,
// goes through pcOnlyFetch: X-Baihe-Local, CSRF when signed in, and a 403
// marks the tab remote so the cards switch to "Run this on the main PC."
import type {
  AutoBackupSettings,
  AutoBackupSettingsUpdate,
  BackupFileDramaList,
  BackupJobStarted,
  DeleteSnapshotDone,
  ImportDramasDone,
  RestoreDramaDone,
  SnapshotDramaList,
  SnapshotInfo,
} from '../types/backups'
import { DELETE_SNAPSHOT_WORD, RESTORE_SNAPSHOT_WORD } from '../types/backups'
import { getJson, postJson, postMultipart } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

const BASE = '/api/backups'

export const getBackupSettings = (f?: Fetch) =>
  getJson<AutoBackupSettings>(`${BASE}/settings`, pcOnlyFetch(f))

/** Send only the fields that changed; the rest stay as they are. */
export const updateBackupSettings = (changes: AutoBackupSettingsUpdate, f?: Fetch) =>
  postJson<AutoBackupSettings>(`${BASE}/settings`, changes, pcOnlyFetch(f))

/** Adds a new copy; the oldest copies then rotate out on the server. */
export const backUpNow = (f?: Fetch) => postJson<BackupJobStarted>(`${BASE}/now`, {}, pcOnlyFetch(f))

export const getSnapshot = (f?: Fetch) => getJson<SnapshotInfo>(`${BASE}/snapshot`, pcOnlyFetch(f))

/** The dramas in the copy called `snapshot`; left out = the server's default
 * copy (a 409 "choose_copy" when there is none). */
export const getSnapshotDramas = (snapshot?: string, f?: Fetch) =>
  getJson<SnapshotDramaList>(
    `${BASE}/snapshot/dramas${snapshot ? `?snapshot=${encodeURIComponent(snapshot)}` : ''}`,
    pcOnlyFetch(f),
  )

/** drama_id is the drama's id inside the copy; `snapshot` left out = the server's default copy. */
export const restoreSnapshotDrama = (drama_id: number, snapshot?: string, f?: Fetch) =>
  postJson<RestoreDramaDone>(
    `${BASE}/snapshot/restore-drama`,
    { drama_id, confirm: true, confirm_text: RESTORE_SNAPSHOT_WORD, ...(snapshot ? { snapshot } : {}) },
    pcOnlyFetch(f),
  )

/** The dramas inside a backup file (a snapshot copy, a manual backup zip or a library.db). Changes nothing. */
export function listBackupFileDramas(file: File | Blob, f?: Fetch) {
  const form = new FormData()
  form.append('file', file)
  return postMultipart<BackupFileDramaList>(`${BASE}/import/list`, form, pcOnlyFetch(f))
}

/** Imports the chosen dramas (ids as listed) as new dramas. The file is sent again: the server keeps nothing between the two calls. */
export function importBackupFileDramas(file: File | Blob, dramaIds: readonly number[], f?: Fetch) {
  const form = new FormData()
  form.append('file', file)
  for (const id of dramaIds) form.append('drama_ids', String(id))
  form.append('confirm', 'true')
  form.append('confirm_text', RESTORE_SNAPSHOT_WORD)
  return postMultipart<ImportDramasDone>(`${BASE}/import`, form, pcOnlyFetch(f))
}

/** Deletes the copy called `snapshot`, or every copy this library manages for
 * `{ all: true }`. The server refuses a request that names neither, so a lost
 * name never deletes everything. A copy this library doesn't manage (another
 * library's, an older or unreadable one) needs `include_unmanaged: true`. */
export const deleteSnapshot = (
  target: ({ snapshot: string } | { all: true }) & { include_unmanaged?: true },
  f?: Fetch,
) =>
  postJson<DeleteSnapshotDone>(
    `${BASE}/snapshot/delete`,
    { confirm: true, confirm_text: DELETE_SNAPSHOT_WORD, ...target },
    pcOnlyFetch(f),
  )
