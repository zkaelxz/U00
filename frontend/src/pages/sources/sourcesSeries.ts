import { ApiError } from '../../api/client'
import { humanize, humanizeValue } from '../../components/labels'
import type { OpenSeries, SeriesChapter, SeriesInfo, SeriesLink, SeriesResult } from '../../types/sources'


export function seriesMeta(display: string, info: SeriesInfo | null, chapters: number): string {
  const parts = [display, `${chapters} chapter${chapters === 1 ? '' : 's'}`]
  if (info?.status) parts.push(humanizeValue(info.status))
  if (info?.language) parts.push(humanize('language', info.language))
  return parts.join(' · ')
}

/** A work's posted download links that are safe to render as links. */
export function seriesLinks(info: SeriesInfo | null): SeriesLink[] {
  return (info?.links ?? []).filter((l) => !!safeHref(l.url)).map((l) => ({ ...l, label: l.label || l.url }))
}

export function seriesExtra(info: SeriesInfo | null): string {
  if (!info) return ''
  return [...(info.authors ?? []), ...(info.genres ?? [])].filter(Boolean).join(' · ')
}

type ChapterGroup = { group: string; chapters: SeriesChapter[] }

/** Chapters by `group`, groups in first-seen order. */
export function groupChapters(chapters: SeriesChapter[]): ChapterGroup[] {
  const out: ChapterGroup[] = []
  for (const c of chapters) {
    const g = c.group ?? ''
    const found = out.find((o) => o.group === g)
    if (found) found.chapters.push(c)
    else out.push({ group: g, chapters: [c] })
  }
  return out
}

/** The first `n` chapters across the groups (empty groups dropped). */
export function limitGroups(groups: ChapterGroup[], n: number): ChapterGroup[] {
  const out: ChapterGroup[] = []
  let left = n
  for (const g of groups) {
    if (left <= 0) break
    const rows = g.chapters.slice(0, left)
    left -= rows.length
    out.push({ group: g.group, chapters: rows })
  }
  return out
}

/** An http(s) link, or null (never render another scheme as a link). */
export function safeHref(url: string | null | undefined): string | null {
  return url && /^https?:\/\//i.test(url) ? url : null
}

/** True when a start's 409 names this same job id as already running. */
export function isSameJobConflict(e: unknown, jobId: string): boolean {
  if (!(e instanceof ApiError) || e.status !== 409) return false
  const d = e.details as { job_id?: unknown } | null | undefined
  return !!d && d.job_id === jobId
}

export interface SeriesJobLike {
  status: 'idle' | 'running' | 'done' | 'error'
  result: SeriesResult | null
  error: unknown
  startedHere: boolean
  startError: unknown
  // Which series a running run is for, when the server says (series jobs).
  runningFor?: { source: string; series_id: string } | null
}

export interface SeriesView {
  status: 'idle' | 'running' | 'done' | 'error'
  result: SeriesResult | null
  error: unknown
  // Another series from this source is loading: a start was refused, or
  // the running run (found on load) reports a different series.
  busyOther: boolean
}

/**
 * What the series panel may show for the open series. The job id is per
 * source, so the job can hold a different series: a finished result counts
 * only when its source and series_id match `open`, and a run found on mount
 * (not started here) that failed is not shown, since it can't be matched.
 * A running run that reports another series (`runningFor`) is `busyOther`.
 */
export function seriesView(open: OpenSeries | null, job: SeriesJobLike, jobId: string | null): SeriesView {
  const busyOther = !!jobId && isSameJobConflict(job.startError, jobId)
  const none: SeriesView = { status: 'idle', result: null, error: null, busyOther }
  if (!open || busyOther) return none
  if (job.status === 'done') {
    const r = job.result
    return r && r.source === open.source && r.series_id === open.series_id
      ? { status: 'done', result: r, error: null, busyOther }
      : none
  }
  if (job.status === 'error') return job.startedHere ? { status: 'error', result: null, error: job.error, busyOther } : none
  const f = job.status === 'running' ? job.runningFor : null
  if (f && (f.source !== open.source || f.series_id !== open.series_id)) {
    // Found running on load for another series of this source.
    return { ...none, busyOther: true }
  }
  return { status: job.status, result: null, error: null, busyOther }
}
