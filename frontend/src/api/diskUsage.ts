// Disk usage (api/routers/disk_usage_routes.py). Every route is PC only, so
// every call, the scan included, goes through pcOnlyFetch.
import type {
  DiskUsageClearDone, DiskUsageMoveDone, DiskUsageScan, DiskUsageTrashEmptyDone, DiskUsageTrashList,
  DiskUsageTrashPurgeDone, DiskUsageTrashRestoreDone, TempCleanDone, UnusedVoiceClip, UnusedVoiceClipList, UnusedVoiceClipTrashDone,
} from '../types/diskUsage'
import { getJson, postJson, withSignal } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

const BASE = '/api/data-usage'

/** The children of one folder ('' = the data folder), biggest first. `signal` cancels the wait. */
export const scanDiskUsage = (path = '', signal?: AbortSignal, f?: Fetch) =>
  getJson<DiskUsageScan>(
    path ? `${BASE}?path=${encodeURIComponent(path)}` : BASE,
    signal ? withSignal(signal, pcOnlyFetch(f)) : pcOnlyFetch(f),
  )

/** Moves one item into Baihe's Trash folder (frees nothing). The size and file count are the ones the person saw (409 if changed). */
export const moveToTrash = (
  item: { path: string; size_bytes: number; file_count: number; irreplaceable?: boolean },
  f?: Fetch,
) =>
  postJson<DiskUsageClearDone>(
    `${BASE}/to-trash`,
    {
      path: item.path,
      confirm: true,
      expected_size_bytes: item.size_bytes,
      expected_file_count: item.file_count,
      ...(item.irreplaceable ? { confirm_irreplaceable: true } : {}),
    },
    pcOnlyFetch(f),
  )

/** Moves a movable item to `destination` (a full folder path on this PC) and repoints Baihe at it. */
export const moveItem = (path: string, destination: string, f?: Fetch) =>
  postJson<DiskUsageMoveDone>(`${BASE}/move`, { path, destination, confirm: true }, pcOnlyFetch(f))

/** What is in Trash, newest first. */
export const listTrash = (f?: Fetch) => getJson<DiskUsageTrashList>(`${BASE}/trash`, pcOnlyFetch(f))

/** Puts a Trash item back where it came from (409 with the reason when it can't). */
export const restoreTrashItem = (id: string, f?: Fetch) =>
  postJson<DiskUsageTrashRestoreDone>(`${BASE}/trash/restore`, { id, confirm: true }, pcOnlyFetch(f))

/** PERMANENTLY deletes one Trash item; the size is the one the person saw (409 if changed). */
export const purgeTrashItem = (item: { id: string; size_bytes: number | null }, f?: Fetch) =>
  postJson<DiskUsageTrashPurgeDone>(
    `${BASE}/trash/purge`,
    { id: item.id, confirm_text: 'DELETE', expected_size_bytes: item.size_bytes },
    pcOnlyFetch(f),
  )

/** PERMANENTLY deletes everything in Trash; the count and total size are those of the list the person saw (409 if changed). */
export const emptyTrash = (seen: { item_count: number; size_bytes: number }, f?: Fetch) =>
  postJson<DiskUsageTrashEmptyDone>(
    `${BASE}/trash/empty`,
    { confirm_text: 'DELETE', expected_item_count: seen.item_count, expected_size_bytes: seen.size_bytes },
    pcOnlyFetch(f),
  )

/** Voice clips no speaker uses, per title (type, size and date only). */
export const listUnusedVoiceClips = (f?: Fetch) =>
  getJson<UnusedVoiceClipList>(`${BASE}/unused-voice-clips`, pcOnlyFetch(f))

/** Moves these clips into Baihe's Trash folder; each carries the size the person saw. Used or changed ones come back in `skipped`. */
export const trashUnusedVoiceClips = (clips: Pick<UnusedVoiceClip, 'id' | 'size_bytes'>[], f?: Fetch) =>
  postJson<UnusedVoiceClipTrashDone>(
    `${BASE}/unused-voice-clips/to-trash`,
    { clips: clips.map((c) => ({ id: c.id, expected_size_bytes: c.size_bytes })), confirm: true },
    pcOnlyFetch(f),
  )

/** Deletes everything in Baihe's own temp folder (409 while a job runs). */
export const cleanTempFiles = (f?: Fetch) => postJson<TempCleanDone>(`${BASE}/clean-temp`, {}, pcOnlyFetch(f))
