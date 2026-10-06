// Pure helpers for the header bell (components/NotificationBell.tsx):
// what counts as unread, the "last seen" mark in localStorage, labels and
// short times. Unit-tested in notificationBell.test.ts.
import type { NotificationItem, NotificationKind } from '../types/notifications'
import type { BadgeTone } from './labels'

export const LAST_SEEN_KEY = 'baihe.notifications.lastSeen'
export const POLL_MS = 60_000

// The newest item the user has seen: its id and time. The server's ids
// start again at 1 when it restarts (the list is in memory only), so an
// item also counts as new when it is newer than the last seen time.
type Seen = { id: number; at: number }

export const NOTHING_SEEN: Seen = { id: 0, at: 0 }

function storage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage
  } catch {
    return null
  }
}

export function readSeen(store: Storage | null = storage()): Seen {
  try {
    const raw = store?.getItem(LAST_SEEN_KEY)
    if (!raw) return NOTHING_SEEN
    const v = JSON.parse(raw) as Partial<Seen> | null
    const id = typeof v?.id === 'number' && Number.isFinite(v.id) ? v.id : 0
    const at = typeof v?.at === 'number' && Number.isFinite(v.at) ? v.at : 0
    return { id, at }
  } catch {
    return NOTHING_SEEN
  }
}

export function writeSeen(seen: Seen, store: Storage | null = storage()): void {
  try {
    store?.setItem(LAST_SEEN_KEY, JSON.stringify(seen))
  } catch {
    // Storage blocked or full: the count just comes back after a reload.
  }
}

export function isUnread(item: NotificationItem, seen: Seen): boolean {
  return item.id > seen.id || item.at > seen.at
}

export function unreadCount(items: NotificationItem[], seen: Seen): number {
  return items.filter((i) => isUnread(i, seen)).length
}

/** The mark after seeing `items`: their newest id and time (unchanged when empty). */
export function seenAfter(items: NotificationItem[], prev: Seen): Seen {
  if (items.length === 0) return prev
  return {
    id: Math.max(...items.map((i) => i.id)),
    at: Math.max(...items.map((i) => i.at)),
  }
}

export function bellLabel(unread: number): string {
  return unread > 0 ? `Notifications (${unread} new)` : 'Notifications'
}

const KIND_BADGE: Record<NotificationKind, { tone: BadgeTone; label: string }> = {
  job_done: { tone: 'ok', label: 'Done' },
  job_failed: { tone: 'bad', label: 'Failed' },
  chapters: { tone: 'info', label: 'New chapters' },
  remote: { tone: 'warn', label: 'Remote access' },
}

export function kindBadge(kind: string): { tone: BadgeTone; label: string } {
  return KIND_BADGE[kind as NotificationKind] ?? { tone: 'neutral', label: 'Update' }
}

/** "just now", "5 min ago", "3 h ago", "2 d ago", then a short date. */
export function shortAgo(at: number, nowSec: number = Date.now() / 1000): string {
  const s = Math.max(0, nowSec - at)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86_400) return `${Math.floor(s / 3600)} h ago`
  if (s < 7 * 86_400) return `${Math.floor(s / 86_400)} d ago`
  return new Date(at * 1000).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}

/** An ISO time for <time dateTime>, or undefined for a bad value. */
export function isoTime(at: number): string | undefined {
  const d = new Date(at * 1000)
  return Number.isFinite(d.getTime()) ? d.toISOString() : undefined
}

export const EMPTY_TEXT = 'Nothing yet. Finished and failed jobs show up here.'
export const KEPT_NOTE = 'This list is kept until the app restarts.'
