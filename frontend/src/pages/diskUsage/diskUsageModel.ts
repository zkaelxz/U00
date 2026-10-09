// Pure helpers for the Disk usage section (DiskUsageSection.tsx).
import type {
  DiskUsageClearDone, DiskUsageItem, DiskUsageMoveDone, DiskUsageScan, DiskUsageTrashItem, TempCleanDone, UnusedVoiceClip,
  UnusedVoiceClipTrashDone,
} from '../../types/diskUsage'
import { formatBytes } from '../libraryAdmin/libraryAdmin'

export { formatBytes }

export const ROOT_LABEL = 'Data folder'
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

export const LINKED_LABEL = 'Linked folder, stored elsewhere'

type LinkedFields = Pick<DiskUsageItem, 'linked_bytes' | 'linked_files' | 'linked_complete'>

const linkedSize = (x: LinkedFields) =>
  `${x.linked_complete === false ? 'at least ' : ''}${formatBytes(x.linked_bytes ?? 0)} · ${filesText(x.linked_files ?? 0)}`

/** What a link points at, kept out of the size above it; null when nothing was measured. */
export function linkedText(item: LinkedFields & Pick<DiskUsageItem, 'is_link'>): string | null {
  if (item.linked_bytes == null) return null
  return item.is_link
    ? `${LINKED_LABEL}: ${linkedSize(item)}`
    : `Plus ${linkedSize(item)} in linked folders stored elsewhere, not counted in the size above.`
}

/** "plus 7.2 GB in linked folders" after the folder's own total, or '' when there are none. */
export const scanLinkedText = (scan: Pick<DiskUsageScan, 'linked_bytes' | 'linked_complete'>) =>
  scan.linked_bytes > 0 ? `plus ${scan.linked_complete ? '' : 'at least '}${formatBytes(scan.linked_bytes)} in linked folders` : ''

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

/** Why Move to Trash is unavailable for this item right now, or null. */
export function clearBlock(item: DiskUsageItem, scan: Pick<DiskUsageScan, 'busy_reason'>): string | null {
  if (item.protected) return item.protected_reason ?? 'Protected.'
  if (scan.busy_reason) return scan.busy_reason
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
  `Confirm: move ${item.name} (${formatBytes(item.size_bytes)}) to Trash`

export const moveConfirmLabel = (item: Pick<DiskUsageItem, 'name' | 'size_bytes'>) =>
  `Confirm: move ${item.name} (${formatBytes(item.size_bytes)})`

export const describeCleared = (r: DiskUsageClearDone) =>
  `Moved ${r.name} (${formatBytes(r.moved_bytes)}) to Trash. Nothing is freed until you delete it from Trash; you can restore it from there.`

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

export const TRASH_WORD = 'DELETE'

/** The always-visible Trash size line. */
export const trashLine = (size_bytes: number, partial = false) =>
  `Trash uses ${partial ? 'at least ' : ''}${formatBytes(size_bytes)}; nothing is freed until you delete from it.`

export const trashItemName = (t: Pick<DiskUsageTrashItem, 'original_path_relative'>) =>
  t.original_path_relative ?? 'Unknown item (its record is missing)'

/** "2026-10-03", or '' when unknown. */
export const trashedOn = (t: Pick<DiskUsageTrashItem, 'trashed_at'>) => (t.trashed_at ?? '').slice(0, 10)

export const trashSizeLine = (t: Pick<DiskUsageTrashItem, 'size_bytes' | 'file_count'>) =>
  t.size_bytes == null || t.file_count == null ? 'Size unknown' : `${formatBytes(t.size_bytes)} · ${filesText(t.file_count)}`

export const describeRestored = (name: string) => `Restored ${name} to where it came from.`

export const describePurged = (name: string, freed: number) =>
  `Deleted ${name} permanently. Freed ${formatBytes(freed)}.`

export const describeEmptied = (r: { freed_bytes: number; removed: number; failed: number }) =>
  r.failed > 0
    ? `Deleted ${r.removed} item${r.removed === 1 ? '' : 's'} (${formatBytes(r.freed_bytes)} freed). ${r.failed} could not be deleted and ${r.failed === 1 ? 'is' : 'are'} still in Trash; they may be in use.`
    : `Emptied Trash: ${r.removed} item${r.removed === 1 ? '' : 's'} deleted, ${formatBytes(r.freed_bytes)} freed.`

export const clipsText = (n: number) => `${n.toLocaleString('en-US')} clip${n === 1 ? '' : 's'}`

export const UNUSED_CLIPS_INTRO = 'Not used by any speaker. Moves to the Baihe trash, where you can restore it.'

export const clipTitle = (title: string) => title.trim() || 'Untitled'

/** "WAV clip · 120 KB · 2026-10-01" (the date is left out when unknown). */
export const clipLine = (c: Pick<UnusedVoiceClip, 'file_type' | 'size_bytes' | 'modified_at'>) =>
  [`${c.file_type.toUpperCase()} clip`, formatBytes(c.size_bytes), (c.modified_at ?? '').slice(0, 10)].filter(Boolean).join(' · ')

export const clipsInUseText = (n: number) =>
  `${n} title${n === 1 ? ' is' : 's are'} left out because a dub, narration or audiobook job is running. Check again when it finishes.`

export const TEMP_CLEAN_INTRO =
  "Baihe keeps its work files for running jobs in its own temp folder and removes them when a job ends. "
  + "If a job was killed or the app crashed, some can be left behind. This deletes all of them."

export const describeTempCleaned = (r: TempCleanDone): string =>
  r.removed === 0
    ? 'No leftover temp files.'
    : `Removed ${r.removed.toLocaleString('en-US')} temp item${r.removed === 1 ? '' : 's'} and freed ${r.freed_mb.toLocaleString('en-US')} MB.`

export function describeClipsMoved(r: UnusedVoiceClipTrashDone): string {
  const moved = r.moved_count === 0
    ? 'No clips were moved.'
    : `Moved ${clipsText(r.moved_count)} (${formatBytes(r.moved_bytes)}) to Trash. Nothing is freed until you delete them from Trash; you can restore them from there.`
  const n = r.skipped.length
  return n === 0 ? moved : `${moved} ${clipsText(n)} skipped: ${n === 1 ? 'it changed or a speaker started using it' : 'they changed or a speaker started using them'}.`
}

/** The server takes at most this many clips per request. */
export const CLIP_BATCH_SIZE = 500

export const clipBatches = <T,>(clips: T[], size = CLIP_BATCH_SIZE): T[][] => {
  const out: T[][] = []
  for (let i = 0; i < clips.length; i += size) out.push(clips.slice(i, i + size))
  return out
}

/** Several batches' results added up (the skipped lists are joined). */
export const sumClipResults = (rs: UnusedVoiceClipTrashDone[]): UnusedVoiceClipTrashDone => ({
  moved_count: rs.reduce((n, r) => n + r.moved_count, 0),
  moved_bytes: rs.reduce((n, r) => n + r.moved_bytes, 0),
  skipped: rs.flatMap((r) => r.skipped),
})

/** What a batch that stopped part-way had already done, from the error's details (0 when it carries none). */
export function clipsDoneBeforeError(details: unknown): UnusedVoiceClipTrashDone {
  const d = (details ?? {}) as Partial<UnusedVoiceClipTrashDone>
  return {
    moved_count: typeof d.moved_count === 'number' ? d.moved_count : 0,
    moved_bytes: typeof d.moved_bytes === 'number' ? d.moved_bytes : 0,
    skipped: Array.isArray(d.skipped) ? d.skipped : [],
  }
}

export function describeClipsStopped(done: UnusedVoiceClipTrashDone, total: number, reason: string): string {
  const skipped = done.skipped.length > 0 ? ` ${clipsText(done.skipped.length)} skipped.` : ''
  return `Moved ${done.moved_count.toLocaleString('en-US')} of ${clipsText(total)} (${formatBytes(done.moved_bytes)}) to Trash; stopped because ${reason.replace(/[.\s]+$/, '')}.${skipped} You can restore them from Trash.`
}
