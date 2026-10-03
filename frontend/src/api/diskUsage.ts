// Disk usage (api/routers/disk_usage_routes.py). Every route is PC only, so
// every call, the scan included, goes through pcOnlyFetch.
import type { DiskUsageClearDone, DiskUsageMoveDone, DiskUsageScan } from '../types/diskUsage'
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

/** Sends one item to the Recycle Bin. The size and file count are the ones the person saw (409 if changed). */
export const recycleItem = (
  item: { path: string; size_bytes: number; file_count: number; irreplaceable?: boolean },
  f?: Fetch,
) =>
  postJson<DiskUsageClearDone>(
    `${BASE}/recycle`,
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
