import { dramaName } from '../components/libraryView'
import { mediaTypeLabel, statusLabel } from '../labels'

// One ordering for every list of titles (Library, its groups, the Series page), so
// "watch in order" means the same thing everywhere.

export type TitleSort = 'added' | 'date-asc' | 'date-desc' | 'title'

export const TITLE_SORTS: [TitleSort, string][] = [
  ['added', 'Date added (newest first)'],
  ['date-asc', 'Stream / release date (oldest first)'],
  ['date-desc', 'Stream / release date (newest first)'],
  ['title', 'Title (A-Z)'],
]

export const isTitleSort = (v: unknown): v is TitleSort => TITLE_SORTS.some(([id]) => id === v)

export interface Sortable {
  id: number
  title_en?: string | null
  title_zh?: string | null
  release_date?: string | null
  episode_number?: number | null
}

const hasDate = (t: Sortable) => !!t.release_date

/** A sorted copy. 'added' keeps the order given (the server sends newest added first).
 *  Titles without a date go last in both date directions, in the order given. */
export function sortTitles<T extends Sortable>(items: readonly T[], sort: TitleSort): T[] {
  const indexed = items.map((t, i) => ({ t, i }))
  const byIndex = (a: { i: number }, b: { i: number }) => a.i - b.i
  if (sort === 'added') return items.slice()
  if (sort === 'title') {
    const key = (t: Sortable) => dramaName({ id: t.id, title_en: t.title_en ?? null, title_zh: t.title_zh ?? null })
    return indexed.sort((a, b) => key(a.t).localeCompare(key(b.t)) || byIndex(a, b)).map((x) => x.t)
  }
  const dir = sort === 'date-asc' ? 1 : -1
  return indexed.sort((a, b) => {
    if (hasDate(a.t) !== hasDate(b.t)) return hasDate(a.t) ? -1 : 1
    if (!hasDate(a.t)) return byIndex(a, b)
    // ISO dates compare correctly as text.
    const d = (a.t.release_date as string).localeCompare(b.t.release_date as string)
    if (d) return dir * d
    // Same day (a stream split in parts): the episode number, then the given order.
    const ea = a.t.episode_number ?? Infinity
    const eb = b.t.episode_number ?? Infinity
    return (ea === eb ? 0 : ea < eb ? -1 : 1) || byIndex(a, b)
  }).map((x) => x.t)
}

/** A series' default order: oldest first once any title has a date, otherwise as sent. */
export const defaultSeriesSort = (items: readonly Sortable[]): TitleSort =>
  items.some(hasDate) ? 'date-asc' : 'added'

// ---- Library grouping -------------------------------------------------------

export type GroupBy = 'none' | 'series' | 'media' | 'status'

export const GROUP_BYS: [GroupBy, string][] = [
  ['none', 'None'], ['series', 'Series'], ['media', 'Media type'], ['status', 'Status'],
]

export const isGroupBy = (v: unknown): v is GroupBy => GROUP_BYS.some(([id]) => id === v)

export const NO_SERIES = 'No series'

export interface GroupableTitle extends Sortable {
  series_id: number | null
  media_type: string | null
  status: string | null
}

export interface TitleGroup<T> {
  /** Stable across reloads, so a collapsed group stays collapsed. */
  key: string
  label: string
  /** Set for a series group: where its header links to. */
  seriesId?: number
  items: T[]
  /** "Anime ×3 · Manga ×2" */
  mix: string
}

const STATUS_ORDER = ['not started', 'aligned', 'translated', 'dubbed', 'exported']

export function mediaMix(items: readonly { media_type: string | null }[]): string {
  const counts = new Map<string, number>()
  for (const t of items) {
    const label = t.media_type ? mediaTypeLabel(t.media_type) : 'Unknown type'
    counts.set(label, (counts.get(label) ?? 0) + 1)
  }
  return [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([label, n]) => `${label} ×${n}`).join(' · ')
}

/** Groups already filtered titles (filters and search apply before this, so an empty
 *  group never appears). `series` is the viewer's visible series (GET /api/series):
 *  a title whose series isn't in it is listed under "No series" rather than naming
 *  a series the viewer can't see. With the default sort, a series' titles go oldest
 *  first by date ("watch in order"); an explicit sort applies everywhere. */
export function groupTitles<T extends GroupableTitle>(
  items: readonly T[], by: GroupBy, sort: TitleSort, series: readonly { id: number; name: string }[],
): TitleGroup<T>[] {
  if (by === 'none') return items.length ? [{ key: 'all', label: '', items: sortTitles(items, sort), mix: '' }] : []
  const buckets = new Map<string, { label: string; seriesId?: number; rank: number; items: T[] }>()
  const names = new Map(series.map((s) => [s.id, s.name]))
  for (const t of items) {
    let key: string
    let label: string
    let seriesId: number | undefined
    let rank = 0
    if (by === 'series') {
      const name = t.series_id !== null ? names.get(t.series_id) : undefined
      if (name !== undefined) { key = `series:${t.series_id}`; label = name; seriesId = t.series_id as number } else { key = 'series:none'; label = NO_SERIES; rank = 1 }
    } else if (by === 'media') {
      key = `media:${t.media_type ?? ''}`
      label = t.media_type ? mediaTypeLabel(t.media_type) : 'Unknown type'
      rank = t.media_type ? 0 : 1
    } else {
      key = `status:${t.status ?? ''}`
      label = t.status ? statusLabel(t.status) : 'No status'
      const at = STATUS_ORDER.indexOf(t.status ?? '')
      rank = t.status ? (at < 0 ? STATUS_ORDER.length : at) : STATUS_ORDER.length + 1
    }
    const bucket = buckets.get(key) ?? { label, seriesId, rank, items: [] }
    bucket.items.push(t)
    buckets.set(key, bucket)
  }
  const inGroup: TitleSort = by === 'series' && sort === 'added' ? 'date-asc' : sort
  return [...buckets].sort((a, b) => a[1].rank - b[1].rank || a[1].label.localeCompare(b[1].label))
    .map(([key, g]) => ({
      key, label: g.label, seriesId: g.seriesId, items: sortTitles(g.items, inGroup), mix: mediaMix(g.items),
    }))
}
