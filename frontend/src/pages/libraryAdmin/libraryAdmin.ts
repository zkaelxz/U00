// Pure helpers for the Library admin UI (selection, result lines, sizes).
import type { DramaSummary } from '../../api/types'
import { TERMINAL_STATUSES, type JobRecord } from '../../types/jobs'
import type {
  LibraryBulkDeleteResult,
  LibraryBulkItem,
  LibraryBulkResult,
  LibraryBulkTranslateSkip,
  LibraryStorageCleanResult,
  LibraryStorageScan,
} from '../../types/libraryAdmin'
import { deleteNotice } from '../libraryForm'

// services/library_admin_service.MAX_BULK_IDS.
export const MAX_SELECTION = 500

type Named = Pick<DramaSummary, 'id' | 'title_en' | 'title_zh'>
export const titleOf = (d: Named) => d.title_en || d.title_zh || `#${d.id}`

export function toggleId(selected: ReadonlySet<number>, id: number): Set<number> {
  const next = new Set(selected)
  if (next.has(id)) next.delete(id)
  else if (next.size < MAX_SELECTION) next.add(id)
  return next
}

/** "Select all visible": the first MAX_SELECTION visible ids; capped says some were left out. */
export function selectAllVisible(visible: readonly Pick<DramaSummary, 'id'>[]): { ids: Set<number>; capped: boolean } {
  return {
    ids: new Set(visible.slice(0, MAX_SELECTION).map((d) => d.id)),
    capped: visible.length > MAX_SELECTION,
  }
}

/** Drop selected ids that are no longer in the (reloaded) list. Same set back when nothing changed. */
export function pruneSelection(selected: Set<number>, items: readonly Pick<DramaSummary, 'id'>[]): Set<number> {
  const present = new Set(items.map((d) => d.id))
  const kept = [...selected].filter((id) => present.has(id))
  return kept.length === selected.size ? selected : new Set(kept)
}

export function selectedItems<T extends Pick<DramaSummary, 'id'>>(items: readonly T[], selected: ReadonlySet<number>): T[] {
  return items.filter((d) => selected.has(d.id))
}

// Bulk translate only takes dramas whose status is "aligned".
export const translatableIds = (items: readonly Pick<DramaSummary, 'id' | 'status'>[]) =>
  items.filter((d) => d.status === 'aligned').map((d) => d.id)

const EXPORTABLE = ['translated', 'dubbed', 'exported']
export const exportableIds = (items: readonly Pick<DramaSummary, 'id' | 'status'>[]) =>
  items.filter((d) => EXPORTABLE.includes(d.status ?? '')).map((d) => d.id)

/** Count of translated/dubbed/exported dramas from the stats' by_status. */
export const exportableCount = (byStatus: Record<string, number> | undefined) =>
  EXPORTABLE.reduce((n, s) => n + (byStatus?.[s] ?? 0), 0)

export const TRANSLATE_NEEDS = "Still needed: a selected title with status 'aligned'."
export const EXPORT_NEEDS =
  "Still needed: a selected title with status 'translated', 'dubbed' or 'exported'."

const ERROR_WORDS: Record<string, string> = {
  not_found: 'not found',
  job_running: 'a job is running',
  delete_failed: 'could not be deleted',
  not_translated: 'not translated yet',
  not_aligned: 'not aligned',
  engine_changed: 'its engine changed',
}
const reasonText = (code: string | null | undefined) => ERROR_WORDS[code ?? ''] ?? 'failed'

type Names = (id: number) => string

/** "Updated 3." or "2 updated, 1 not found." */
export function describeBulkResult(r: LibraryBulkResult): string {
  const failed = r.results.filter((x) => !x.ok)
  if (!failed.length) return `Updated ${r.updated}.`
  const counts = new Map<string, number>()
  for (const x of failed) counts.set(reasonText(x.error), (counts.get(reasonText(x.error)) ?? 0) + 1)
  return `${r.updated} updated, ${[...counts].map(([why, n]) => `${n} ${why}`).join(', ')}.`
}

function skippedList(items: { id: number; why: string }[], names: Names): string {
  return items.map((x) => `${names(x.id)} (${x.why})`).join(', ')
}

/** "Deleted 2. Skipped: Title (a job is running)." plus any left-behind-files warnings. */
export function describeDeleteResult(r: LibraryBulkDeleteResult, names: Names): string {
  const skipped = r.results.filter((x) => !x.ok).map((x) => ({ id: x.drama_id, why: reasonText(x.error) }))
  const warnings = r.results
    .filter((x) => x.ok)
    .map((x: LibraryBulkItem) => deleteNotice({ deleted: true, drama_id: x.drama_id, warning: x.warning }))
    .filter(Boolean)
  return [
    `Deleted ${r.deleted}.`,
    skipped.length ? `Skipped: ${skippedList(skipped, names)}.` : '',
    ...warnings,
  ].filter(Boolean).join(' ')
}

/** "1 skipped: not aligned (Title)" or null. */
export function describeTranslateSkips(skips: readonly LibraryBulkTranslateSkip[], names: Names): string | null {
  if (!skips.length) return null
  const parts = skips.map((s) => `${reasonText(s.reason)} (${names(s.drama_id)})`)
  return `${skips.length} skipped: ${parts.join(', ')}`
}

/** Decimal units, one decimal from KB up: "48.2 GB", "512 B". */
export function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n < 1000) return `${Math.max(0, Math.round(n || 0))} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let v = n
  let i = -1
  while (v >= 1000 && i < units.length - 1) {
    v /= 1000
    i += 1
  }
  return `${v.toFixed(1)} ${units[i]}`
}

/** "Library 48.2 GB · 3.1 GB reclaimable · this preset frees 2.4 GB" */
export const describeScan = (s: LibraryStorageScan) =>
  `Library ${formatBytes(s.total_bytes)} · ${formatBytes(s.reclaimable_bytes)} reclaimable · ` +
  `this preset frees ${formatBytes(s.would_free_bytes)}`

/** "Freed 2.3 GB. 1 drama skipped (job running)." */
export function describeClean(r: LibraryStorageCleanResult): string {
  const skipped = r.results.filter((x) => !x.ok)
  const running = skipped.filter((x) => x.error === 'job_running').length
  const other = skipped.length - running
  return [
    `Freed ${formatBytes(r.freed_bytes)}.`,
    running ? `${running} title${running === 1 ? '' : 's'} skipped (job running).` : '',
    other ? `${other} title${other === 1 ? '' : 's'} could not be cleaned.` : '',
  ].filter(Boolean).join(' ')
}

/** Any job queued or running (restore waits for all of them). */
export const anyJobActive = (jobs: readonly Pick<JobRecord, 'status'>[]) =>
  jobs.some((j) => !TERMINAL_STATUSES.includes(j.status))

/** "40%" from a 0..1 progress, or '' when unknown. */
export const percent = (p: number | null | undefined) =>
  p == null ? '' : `${Math.round(Math.min(Math.max(p, 0), 1) * 100)}%`
