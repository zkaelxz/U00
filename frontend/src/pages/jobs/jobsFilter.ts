// Pure filter, sort and link logic for the Jobs page (pages/Jobs.tsx).
import type { Route } from '../../router'
import { isActive, isFinished } from '../diagnosticsFormat'
import { shownStatus, type JobKind, type JobPage, type JobRecord } from '../../types/jobs'
import { STAGE_LABELS, type StageId } from '../workspace/stages'

type StatusFilter = 'active' | 'failed' | 'finished' | 'cancelled' | 'all'
export type KindFilter = 'all' | 'transcribe' | 'translate' | 'review' | 'dub' | 'export' | 'sources' | 'other'
export type RangeFilter = 'all' | 'today' | 'week'
export type SortKey = 'default' | 'started' | 'duration'
export type SortDir = 'asc' | 'desc'

export interface JobFilters {
  // '' lets the page choose: Active while anything is active, else All.
  status: StatusFilter | ''
  search: string
  kind: KindFilter
  range: RangeFilter
  mine: boolean
}

export const NO_FILTERS: JobFilters = { status: '', search: '', kind: 'all', range: 'all', mine: false }

export const STATUS_CHIPS: { id: StatusFilter; label: string }[] = [
  { id: 'active', label: 'Active' },
  { id: 'failed', label: 'Failed' },
  { id: 'finished', label: 'Finished' },
  { id: 'cancelled', label: 'Cancelled' },
  { id: 'all', label: 'All' },
]

export const KIND_OPTIONS: { id: KindFilter; label: string }[] = [
  { id: 'all', label: 'All kinds' },
  { id: 'transcribe', label: 'Transcribe' },
  { id: 'translate', label: 'Translate' },
  { id: 'review', label: 'Review checks' },
  { id: 'dub', label: 'Dub' },
  { id: 'export', label: 'Export' },
  { id: 'sources', label: 'Sources' },
  { id: 'other', label: 'Other' },
]

export const RANGE_OPTIONS: { id: RangeFilter; label: string }[] = [
  { id: 'all', label: 'Any time' },
  { id: 'today', label: 'Today' },
  { id: 'week', label: 'Last 7 days' },
]

const DAY_S = 86_400

// Every list, pill and count reads the status through shownStatus, so a job the server calls done whose outcome failed is Failed everywhere.
const isFailed = (j: Pick<JobRecord, 'status' | 'outcome'>) => shownStatus(j) === 'error'

// A run stopped by Cancel can end 'done' with a cancelled outcome when the job noticed the cancel itself.
const isCancelled = (j: Pick<JobRecord, 'status' | 'outcome'>) => shownStatus(j) === 'cancelled'

// Each ended job lands in exactly one of Failed, Cancelled and Finished, so the chips add up to All.
const matchesStatus = (j: JobRecord, status: StatusFilter) =>
  status === 'all' ||
  (status === 'active' && isActive(j.status)) ||
  (status === 'failed' && isFailed(j)) ||
  (status === 'cancelled' && isCancelled(j)) ||
  (status === 'finished' && isFinished(j.status) && shownStatus(j) === 'done')

// `align` (resegment, resplit) is not worth a chip of its own.
export function kindGroup(kind: JobKind | undefined): Exclude<KindFilter, 'all'> {
  switch (kind) {
    case 'transcribe': case 'translate': case 'review': case 'dub': case 'export':
      return kind
    case 'import':
      return 'sources'
    default:
      return 'other'
  }
}

/** The status the page shows when the viewer hasn't picked one. */
export function effectiveStatus(filters: JobFilters, jobs: JobRecord[]): StatusFilter {
  if (filters.status) return filters.status
  return jobs.some((j) => isActive(j.status)) ? 'active' : 'all'
}

interface StatusCounts { active: number; failed: number; finished: number; cancelled: number; all: number }

export function statusCounts(jobs: JobRecord[]): StatusCounts {
  const n = (s: StatusFilter) => jobs.filter((j) => matchesStatus(j, s)).length
  return { active: n('active'), failed: n('failed'), finished: n('finished'), cancelled: n('cancelled'), all: jobs.length }
}

// A queued job has no start time yet; its last update is the best date for it.
const jobTime = (j: JobRecord) => j.started_at ?? j.updated_at

/**
 * Jobs that pass every filter. Running and queued jobs ignore the time range:
 * hiding one the viewer is waiting on would be worse than showing an old one.
 * `titles` maps drama ids to names for the search box.
 */
export function filterJobs(
  jobs: JobRecord[],
  filters: JobFilters,
  titles: ReadonlyMap<number, string>,
  nowSec: number,
): JobRecord[] {
  const status = effectiveStatus(filters, jobs)
  const needle = filters.search.trim().toLowerCase()
  const since = filters.range === 'today' ? nowSec - DAY_S : filters.range === 'week' ? nowSec - 7 * DAY_S : null
  return jobs.filter((j) => {
    if (!matchesStatus(j, status)) return false
    if (filters.kind !== 'all' && kindGroup(j.kind) !== filters.kind) return false
    if (filters.mine && j.owned_by_me !== true) return false
    if (since !== null && !isActive(j.status) && jobTime(j) < since) return false
    if (needle) {
      const title = j.drama_id != null ? titles.get(j.drama_id) ?? '' : ''
      if (!`${j.description ?? ''} ${title} ${j.job_id}`.toLowerCase().includes(needle)) return false
    }
    return true
  })
}

export const hasActiveFilters = (f: JobFilters) =>
  f.search.trim() !== '' || f.kind !== 'all' || f.range !== 'all' || f.mine

const durationOf = (j: JobRecord, nowSec: number) => (j.started_at == null ? null : (j.finished_at ?? nowSec) - j.started_at)

/**
 * Running jobs first, then queued, then the rest. Within each group: newest
 * started first by default, or by the chosen column. A job without a value
 * (not started) sorts last whichever way the column runs.
 */
export function sortJobs(jobs: JobRecord[], key: SortKey, dir: SortDir, nowSec: number): JobRecord[] {
  const value = (j: JobRecord): number | null => (key === 'duration' ? durationOf(j, nowSec) : j.started_at ?? null)
  const sign = key === 'default' ? -1 : dir === 'asc' ? 1 : -1
  const within = (a: JobRecord, b: JobRecord) => {
    const va = key === 'default' ? jobTime(a) : value(a)
    const vb = key === 'default' ? jobTime(b) : value(b)
    if (va === null && vb === null) return 0
    if (va === null) return 1
    if (vb === null) return -1
    return sign * (va - vb)
  }
  const rank = (j: JobRecord) => (j.status === 'running' ? 0 : j.status === 'queued' ? 1 : 2)
  // Array.prototype.sort is stable, so ties keep the server's order.
  return [...jobs].sort((a, b) => rank(a) - rank(b) || within(a, b))
}

const STAGE_BY_KIND: Partial<Record<JobKind, StageId>> = {
  transcribe: 'source',
  import: 'source',
  translate: 'translate',
  review: 'review',
  align: 'review',
  export: 'export',
}

interface JobLinks {
  title: Route | null
  stage: { label: string; route: Route } | null
}

const PAGE_LINKS: Partial<Record<JobPage, { label: string; route: Route }>> = {
  sources: { label: 'Sources', route: { name: 'sources' } },
  discover: { label: 'Discover', route: { name: 'discover' } },
  live: { label: 'Live', route: { name: 'live' } },
  settings: { label: 'Settings', route: { name: 'settings' } },
  // Every job that names this page is an install or update check, which live on the Packages card.
  diagnostics: { label: 'Diagnostics', route: { name: 'diagnostics', card: 'packages' } },
}

/** Where a job's title and stage live. A job with no title links to the page the server says it belongs to, if any. */
export function jobLinks(j: Pick<JobRecord, 'drama_id' | 'kind' | 'page'> & { job_id?: string }): JobLinks {
  if (j.drama_id == null) return { title: null, stage: j.page ? PAGE_LINKS[j.page] ?? null : null }
  // A transcription comparison's results are in Review's History tab, not the Media stage.
  if (j.job_id?.startsWith('comparetx_')) {
    return {
      title: { name: 'drama', id: j.drama_id, stage: null },
      stage: { label: STAGE_LABELS.review, route: { name: 'drama', id: j.drama_id, stage: 'review', tools: 'history', focus: 'compare' } },
    }
  }
  const stage = j.kind ? STAGE_BY_KIND[j.kind] : undefined
  return {
    title: { name: 'drama', id: j.drama_id, stage: null },
    stage: stage ? { label: STAGE_LABELS[stage], route: { name: 'drama', id: j.drama_id, stage } } : null,
  }
}

export const progressPercent = (j: Pick<JobRecord, 'status' | 'progress'>): number | null =>
  isActive(j.status) && j.progress != null ? Math.max(0, Math.min(100, Math.round(j.progress * 100))) : null

/** Filters read back from storage: anything unknown or malformed falls back to its default. */
export function normalizeFilters(raw: unknown): JobFilters {
  const r = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>
  const pick = <T extends string>(v: unknown, allowed: readonly T[], fallback: T): T =>
    typeof v === 'string' && (allowed as readonly string[]).includes(v) ? (v as T) : fallback
  return {
    status: pick(r.status, ['', ...STATUS_CHIPS.map((c) => c.id)] as const, ''),
    search: typeof r.search === 'string' ? r.search.slice(0, 200) : '',
    kind: pick(r.kind, KIND_OPTIONS.map((k) => k.id), 'all'),
    range: pick(r.range, RANGE_OPTIONS.map((o) => o.id), 'all'),
    mine: r.mine === true,
  }
}

/** "just now", "5 min ago", "3 h ago", "2 d ago" for an epoch-seconds time. */
export function relativeTime(sec: number, nowSec: number): string {
  const d = Math.max(0, nowSec - sec)
  if (d < 60) return 'just now'
  if (d < 3600) return `${Math.floor(d / 60)} min ago`
  if (d < DAY_S) return `${Math.floor(d / 3600)} h ago`
  return `${Math.floor(d / DAY_S)} d ago`
}
