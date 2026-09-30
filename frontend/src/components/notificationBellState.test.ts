import { describe, expect, it } from 'vitest'

import type { NotificationItem } from '../types/notifications'
import {
  LAST_SEEN_KEY,
  NOTHING_SEEN,
  bellLabel,
  isUnread,
  isoTime,
  kindBadge,
  readSeen,
  seenAfter,
  shortAgo,
  unreadCount,
  writeSeen,
} from './notificationBellState'

const item = (id: number, at: number, kind: NotificationItem['kind'] = 'job_done'): NotificationItem => ({
  id,
  at,
  kind,
  text: `event ${id}`,
})

function memoryStorage(): Storage {
  const m = new Map<string, string>()
  return {
    get length() {
      return m.size
    },
    clear: () => m.clear(),
    getItem: (k) => m.get(k) ?? null,
    key: (i) => [...m.keys()][i] ?? null,
    removeItem: (k) => void m.delete(k),
    setItem: (k, v) => void m.set(k, String(v)),
  }
}

function throwingStorage(): Storage {
  const boom = () => {
    throw new Error('blocked')
  }
  return { length: 0, clear: boom, getItem: boom, key: boom, removeItem: boom, setItem: boom }
}

describe('unread count', () => {
  const items = [item(3, 300), item(2, 200), item(1, 100)] // newest first, like the API

  it('counts everything when nothing was seen', () => {
    expect(unreadCount(items, NOTHING_SEEN)).toBe(3)
  })

  it('counts items newer than the last seen one', () => {
    expect(unreadCount(items, { id: 2, at: 200 })).toBe(1)
    expect(unreadCount(items, { id: 3, at: 300 })).toBe(0)
  })

  it('after a server restart (ids start again at 1) newer events still count', () => {
    const seen = { id: 40, at: 1000 }
    const afterRestart = [item(2, 1200), item(1, 1100)]
    expect(unreadCount(afterRestart, seen)).toBe(2)
    expect(isUnread(item(1, 900), seen)).toBe(false)
  })

  it('seeing the list marks its newest id and time; an empty list keeps the mark', () => {
    expect(seenAfter(items, NOTHING_SEEN)).toEqual({ id: 3, at: 300 })
    expect(seenAfter([], { id: 9, at: 9 })).toEqual({ id: 9, at: 9 })
    expect(unreadCount(items, seenAfter(items, NOTHING_SEEN))).toBe(0)
  })
})

describe('last seen in localStorage', () => {
  it('round-trips the mark', () => {
    const store = memoryStorage()
    expect(readSeen(store)).toEqual(NOTHING_SEEN)
    writeSeen({ id: 7, at: 1234.5 }, store)
    expect(store.getItem(LAST_SEEN_KEY)).toBe('{"id":7,"at":1234.5}')
    expect(readSeen(store)).toEqual({ id: 7, at: 1234.5 })
  })

  it('ignores junk and blocked storage', () => {
    const store = memoryStorage()
    store.setItem(LAST_SEEN_KEY, 'not json')
    expect(readSeen(store)).toEqual(NOTHING_SEEN)
    store.setItem(LAST_SEEN_KEY, '{"id":"7","at":null}')
    expect(readSeen(store)).toEqual(NOTHING_SEEN)
    expect(readSeen(throwingStorage())).toEqual(NOTHING_SEEN)
    expect(() => writeSeen({ id: 1, at: 1 }, throwingStorage())).not.toThrow()
    expect(readSeen(null)).toEqual(NOTHING_SEEN)
  })
})

describe('labels', () => {
  it('names the bell with the unread count', () => {
    expect(bellLabel(0)).toBe('Notifications')
    expect(bellLabel(3)).toBe('Notifications (3 new)')
  })

  it('gives each kind a toned, worded badge', () => {
    expect(kindBadge('job_failed')).toEqual({ tone: 'bad', label: 'Failed' })
    expect(kindBadge('job_done')).toEqual({ tone: 'ok', label: 'Done' })
    expect(kindBadge('chapters')).toEqual({ tone: 'info', label: 'New chapters' })
    expect(kindBadge('something_else')).toEqual({ tone: 'neutral', label: 'Update' })
  })

  it('short relative times', () => {
    const now = 1_000_000
    expect(shortAgo(now - 5, now)).toBe('just now')
    expect(shortAgo(now + 30, now)).toBe('just now') // clock skew never shows "in the future"
    expect(shortAgo(now - 5 * 60, now)).toBe('5 min ago')
    expect(shortAgo(now - 3 * 3600, now)).toBe('3 h ago')
    expect(shortAgo(now - 2 * 86_400, now)).toBe('2 d ago')
    expect(shortAgo(now - 30 * 86_400, now)).not.toMatch(/ago/)
    expect(isoTime(0)).toBe('1970-01-01T00:00:00.000Z')
    expect(isoTime(Number.NaN)).toBeUndefined()
  })
})
