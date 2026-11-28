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
import { getJson, postJson, postMultipart, type Fetch } from './client'
import { pcOnlyFetch } from './pcOnly'

const BASE = '/api/backups'

export const getBackupSettings = (f?: Fetch) =>
  getJson<AutoBackupSettings>(`${BASE}/settings`, pcOnlyFetch(f))

/** Send only the fields that changed; the rest stay as they are. */
export const updateBackupSettings = (changes: AutoBackupSettingsUpdate, f?: Fetch) =>
  postJson<AutoBackupSettings>(`${BASE}/settings`, changes, pcOnlyFetch(f))

/** The owner has read the same-drive note; the server never raises it again. */
export const dismissSameDriveNote = (f?: Fetch) =>
  postJson<AutoBackupSettings>(`${BASE}/same-drive-note/dismiss`, {}, pcOnlyFetch(f))

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

export interface ImportChoices {
  /** Merge each series into one of the same name here instead of adding a new series. */
  joinSeries?: boolean
  /** Add the file's voice bank entries. */
  voiceBank?: boolean
  /** Take the file's Translation and Preferences settings (never keys or paths). */
  settings?: boolean
}

/** Imports the chosen dramas (ids as listed) as new dramas. The file is sent again: the server keeps nothing between the two calls. */
export function importBackupFileDramas(file: File | Blob, dramaIds: readonly number[], f?: Fetch, choices: ImportChoices = {}) {
  const form = new FormData()
  form.append('file', file)
  for (const id of dramaIds) form.append('drama_ids', String(id))
  if (choices.joinSeries) form.append('join_series', 'true')
  if (choices.voiceBank) form.append('voice_bank', 'true')
  if (choices.settings) form.append('settings', 'true')
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
