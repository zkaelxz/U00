// Pure helpers for the Disk usage section (DiskUsageSection.tsx).
import type { DiskUsageClearDone, DiskUsageItem, DiskUsageMoveDone, DiskUsageScan } from '../../types/diskUsage'
import { formatBytes } from '../libraryAdmin/libraryAdmin'

export { formatBytes }

export const ROOT_LABEL = 'Data folder'
export const NO_RECYCLE_BIN_TEXT = 'Clearing sends items to the Windows Recycle Bin, and this system has none.'
export const LINK_INSIDE_TEXT = "This folder contains a link or junction, so it can't be cleared whole. Open it and clear items inside instead."
export const PARTIAL_TEXT: Record<'entries' | 'time' | 'items', string> = {
  entries: 'This folder has more files than can be counted at once. Sizes shown are at least this much.',
  time: 'Counting took too long and was stopped. Sizes shown are at least this much.',
  items: 'This folder holds more items than can be listed.',
}

export type Crumb = { label: string; path: string }

/** "library/dramas/1" -> Data folder > library > dramas > 1, each with the path it opens. */
export function crumbs(path: string): Crumb[] {
  const parts = path ? path.split('/') : []
  return [
    { label: ROOT_LABEL, path: '' },
    ...parts.map((label, i) => ({ label, path: parts.slice(0, i + 1).join('/') })),
  ]
}

export const filesText = (n: number) => `${n.toLocaleString('en-US')} file${n === 1 ? '' : 's'}`

/** "1.2 GB · 340 files" */
export const sizeLine = (item: Pick<DiskUsageItem, 'size_bytes' | 'file_count' | 'complete'>) =>
  `${item.complete ? '' : 'at least '}${formatBytes(item.size_bytes)} · ${filesText(item.file_count)}`

/** Bar width in percent: a visible sliver for anything non-empty, never past 100. */
export function barPercent(item: Pick<DiskUsageItem, 'percent_of_parent' | 'size_bytes'>): number {
  if (item.size_bytes <= 0) return 0
  return Math.min(100, Math.max(1.5, item.percent_of_parent))
}

export const percentText = (p: number) => (p > 0 && p < 0.1 ? '<0.1%' : `${p.toFixed(1)}%`)

export type Tone = 'protected' | 'irreplaceable' | 'regenerable' | 'plain'

export function itemTone(item: DiskUsageItem): Tone {
  if (item.protected) return 'protected'
  if (item.irreplaceable) return 'irreplaceable'
  if (item.regenerable) return 'regenerable'
  return 'plain'
}

/** Why Clear is unavailable for this item right now, or null. */
export function clearBlock(item: DiskUsageItem, scan: Pick<DiskUsageScan, 'busy_reason' | 'recycle_available' | 'recycle_reason'>): string | null {
  if (item.protected) return item.protected_reason ?? 'Protected.'
  if (scan.busy_reason) return scan.busy_reason
  if (!scan.recycle_available) return scan.recycle_reason || NO_RECYCLE_BIN_TEXT
  if (item.contains_link) return LINK_INSIDE_TEXT
  if (!item.complete) return 'This could not be fully counted, so its size can not be checked before clearing. Open it and clear pieces.'
  return null
}

/** Why Move is unavailable, or null. */
export function moveBlock(item: DiskUsageItem, scan: Pick<DiskUsageScan, 'busy_reason'>): string | null {
  if (!item.movable.supported) return item.movable.reason ?? 'This can not be moved.'
  if (scan.busy_reason) return scan.busy_reason
  return null
}

export const clearConfirmLabel = (item: Pick<DiskUsageItem, 'name' | 'size_bytes'>) =>
  `Confirm: send ${item.name} (${formatBytes(item.size_bytes)}) to the Recycle Bin`

export const moveConfirmLabel = (item: Pick<DiskUsageItem, 'name' | 'size_bytes'>) =>
  `Confirm: move ${item.name} (${formatBytes(item.size_bytes)})`

export const describeCleared = (r: DiskUsageClearDone) =>
  `Sent ${r.name} to the Recycle Bin. Freed ${formatBytes(r.freed_bytes)}; you can restore it from there.`

export const describeMoved = (r: DiskUsageMoveDone) =>
  `Moved ${r.name} (${formatBytes(r.moved_bytes)}). Baihe now uses the new folder.`

/** "123.4 GB free of 500.0 GB", or '' when unknown. */
export function diskLine(scan: Pick<DiskUsageScan, 'disk_free_bytes' | 'disk_total_bytes'>): string {
  if (scan.disk_free_bytes == null || scan.disk_total_bytes == null) return ''
  return `${formatBytes(scan.disk_free_bytes)} free of ${formatBytes(scan.disk_total_bytes)} on this drive`
}

/** Treemap cell label: name, plus the size when the cell is big enough (units out of 100 wide). */
export function cellLabel(item: DiskUsageItem, w: number, h: number): string {
  if (w < 9 || h < 9) return ''
  return h >= 16 && w >= 14 ? `${item.name}\n${formatBytes(item.size_bytes)}` : item.name
}
