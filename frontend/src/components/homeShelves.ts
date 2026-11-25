// Pure helpers for the Library's Ready, Needs you and Running shelves (HomeShelves.tsx).

import { formatClock, pluralLocale } from '../labels'
import { titleKind } from '../pages/workspace/titleKind'
import { routeHref } from '../router'
import type { JobRecord } from '../types/jobs'
import type { LibraryNeedsYouEntry, LibraryReadyEntry, LibraryRunningEntry } from '../types/library'
import { readAction, workspaceHref } from './libraryView'

export type ShelfAction = { label: string; href: string }

/** "2 h 05 m" past an hour, else "45 min" (never "0 min"). */
export function formatLength(seconds: number): string {
  const minutes = Math.max(1, Math.round(seconds / 60))
  if (minutes < 60) return `${minutes} min`
  return `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, '0')} m`
}

/** The small tag on a Ready card: what kind of thing it is. */
export function kindTag(e: Pick<LibraryReadyEntry, 'media_type' | 'content_mode'>): string {
  if (e.media_type === 'streamer_vod' || e.content_mode === 'streamer_vod') return 'Stream'
  const kind = titleKind(e.media_type, e.content_mode)
  return { video: 'Video', audio: 'Audio', comic: 'Comic', novel: 'Novel', subtitle: 'Subtitles' }[kind]
}

export interface ReadyCard {
  href: string
  label: string
  /** 0 to 1 once started, null for one not started yet (which shows New). */
  progress: number | null
}

/** A Ready card's link and wording. `watchedAt` is where the Watch page last stopped on this device (0 for never). */
export function readyCard(e: LibraryReadyEntry, watchedAt: number): ReadyCard {
  const { href } = readAction({ id: e.drama_id, media_type: e.media_type ?? null, content_mode: e.content_mode })
  const kind = titleKind(e.media_type, e.content_mode)
  if (kind === 'video' || kind === 'audio') {
    const length = e.length_seconds ?? 0
    if (watchedAt > 0) return { href, label: `Continue at ${formatClock(watchedAt)}`, progress: length > 0 ? Math.min(1, watchedAt / length) : null }
    const verb = kind === 'video' ? 'Watch' : 'Listen'
    return { href, label: length > 0 ? `${verb} · ${formatLength(length)}` : verb, progress: null }
  }
  if (e.read_percent > 0) return { href, label: 'Continue reading', progress: Math.min(1, e.read_percent / 100) }
  if (kind === 'comic' && e.page_count > 0) return { href, label: `Read · ${pluralLocale(e.page_count, 'page')}`, progress: null }
  return { href, label: 'Read', progress: null }
}

/** Started cards first, then the rest in the server's newest-first order. */
export function readyOrder<T extends { progress: number | null }>(cards: T[]): T[] {
  return [...cards.filter((c) => c.progress !== null), ...cards.filter((c) => c.progress === null)]
}

const STEP_VERB: Record<string, string> = {
  transcribe: 'Transcribing', translate: 'Translating', export: 'Exporting', autoqc: 'Checking', auto_qc: 'Checking',
}
export const stepVerb = (step: string | null | undefined): string => (step && STEP_VERB[step]) || 'Working on'

// A refused or missing key is the one failure the person fixes in Settings rather than in Jobs.
const KEY_TROUBLE = /\b(api )?key\b/i

export interface NeedsRow { text: string; action: ShelfAction | null }

export interface NeedsViewer {
  /** May retry and reorder queue items (the PC owner and admins). */
  canControlQueue: boolean
  /** May open Settings to change a key: at the PC. */
  canOpenSettings: boolean
  /** Holds lines.edit, so Check is a button they can use. */
  canEditLines: boolean
}

/** One Needs you row, or null when the viewer could do nothing with it. */
export function needsRow(e: LibraryNeedsYouEntry, viewer: NeedsViewer): NeedsRow | null {
  if (e.kind === 'failed') {
    if (!viewer.canControlQueue) return { text: 'Stopped. The PC owner has been told.', action: null }
    const reason = e.reason?.trim()
    const text = `${stepVerb(e.step)} stopped${reason ? `: ${reason}` : ''}`
    if (reason && KEY_TROUBLE.test(reason) && viewer.canOpenSettings) {
      return { text, action: { label: 'Fix the key', href: routeHref({ name: 'settings', card: 'engine-routing' }) } }
    }
    return { text, action: { label: 'See what happened', href: routeHref({ name: 'jobs' }) } }
  }
  if (e.kind === 'series_hold' && e.series_id !== null && e.series_id !== undefined) {
    const href = routeHref({ name: 'series', id: e.series_id })
    return { text: e.reason ?? 'Its auto-run is waiting for you.', action: { label: e.hold_kind === 'review' ? 'See episode 1' : 'Open the series', href } }
  }
  if (e.kind === 'check' && e.drama_id !== null && e.drama_id !== undefined && e.risk_count) {
    if (!viewer.canEditLines) return null
    const kind = titleKind(e.media_type, e.content_mode)
    const unsure = `${pluralLocale(e.risk_count, 'line')} look${e.risk_count === 1 ? 's' : ''} unsure`
    const text = kind === 'video' || kind === 'audio' ? `You can watch it now. ${unsure}` : unsure
    // Watch steps through the same lines while it plays, so a title that plays is checked there.
    const href = kind === 'video' || kind === 'audio' ? routeHref({ name: 'watch', id: e.drama_id }) : workspaceHref(e.drama_id, 'review')
    return { text, action: { label: `Check ${pluralLocale(e.risk_count, 'line')}`, href } }
  }
  return null
}

export interface RunningLine { text: string; progress: number | null; left: string | null; waiting: string | null }

/** The one Running line: the step that is running (or first in line), and how many wait behind it. */
export function runningLine(entries: LibraryRunningEntry[], total: number, jobs: JobRecord[] | null, now: number): RunningLine | null {
  const first = entries.find((e) => e.status === 'running') ?? entries[0]
  if (!first) return null
  const job = first.job_id ? jobs?.find((j) => j.job_id === first.job_id) : undefined
  const progress = first.status === 'running' && typeof job?.progress === 'number' ? job.progress : null
  const others = total - 1
  return {
    text: first.status === 'running' ? `${stepVerb(first.step)} ${first.title}` : `Next: ${stepVerb(first.step).toLowerCase()} ${first.title}`,
    progress,
    left: timeLeft(progress, job?.started_at ?? null, now),
    waiting: others > 0 ? `${others} more ${others === 1 ? 'is' : 'are'} waiting ${others === 1 ? 'its' : 'their'} turn` : null,
  }
}

/** "18 min left" from how long the step has run and how far it got; null while too early to tell. */
export function timeLeft(progress: number | null, startedAt: number | null, now: number): string | null {
  if (progress === null || startedAt === null || progress < 0.03 || progress >= 1) return null
  const elapsed = now - startedAt
  if (elapsed <= 0) return null
  const left = (elapsed * (1 - progress)) / progress
  return `${formatLength(left)} left`
}
