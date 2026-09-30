/*
 * The header bell (Step 44): recent finished/failed jobs and new-chapter
 * finds from GET /api/notifications (library.read, so household users away
 * from the PC see it too). The list lives in the server's memory and is
 * lost when the app restarts.
 *
 * Reads on mount, after every (re)connect of the push stream (GET
 * /api/events pushes the list when it changes) and when the window regains
 * focus; only while the stream is down does it poll, every POLL_MS while the
 * tab is visible. Errors are quiet: a failed read keeps the last list, and a
 * refusal (401/403) or a server without the route (404) hides the bell and
 * stops reading. The unread count is the items newer than a "last
 * seen" mark in localStorage; opening the panel marks them all seen (they
 * stay marked "New" until it closes).
 */
import { useCallback, useEffect, useId, useRef, useState } from 'react'

import { ApiError } from '../api/client'
import { listNotifications } from '../api/notifications'
import { useEventStream } from '../hooks/useEventStream'
import type { NotificationItem, NotificationList } from '../types/notifications'
import { Badge } from './Badge'
import {
  EMPTY_TEXT,
  KEPT_NOTE,
  POLL_MS,
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
import './notificationBell.css'

const HIDE_ON = [401, 403, 404]
const PANEL_REM = 24 // .notify-panel width in notificationBell.css
const GUTTER = 16

// Room for the panel to the left of the bell's right edge? (null: unknown.)
function panelFitsLeftwards(el: HTMLElement | null): boolean | null {
  if (!el || typeof window === 'undefined') return null
  const rem = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16
  const width = Math.min(PANEL_REM * rem, window.innerWidth - 2 * GUTTER)
  return el.getBoundingClientRect().right - width >= GUTTER
}

export function NotificationBell() {
  const [items, setItems] = useState<NotificationItem[]>([])
  const [seen, setSeen] = useState(readSeen)
  const seenRef = useRef(seen)
  const [hidden, setHidden] = useState(false)
  const [open, setOpen] = useState(false)
  const openRef = useRef(false)
  // Opens leftwards from the bell unless that would run off the screen (the
  // header wraps the bell to the left edge on narrower desktops).
  const [alignStart, setAlignStart] = useState(false)
  // Items that were unread when the panel opened: shown as "New" until it closes.
  const [fresh, setFresh] = useState<Set<number>>(() => new Set())
  const wrap = useRef<HTMLDivElement>(null)
  const button = useRef<HTMLButtonElement>(null)
  const panelId = useId()

  // Everything listed while the panel is open counts as seen (including
  // items a poll adds then); what was unread is kept in `fresh` as "New".
  const markSeen = useCallback((list: NotificationItem[]) => {
    const prev = seenRef.current
    const unread = list.filter((i) => isUnread(i, prev))
    if (unread.length === 0) return
    setFresh((cur) => new Set([...cur, ...unread.map((i) => i.id)]))
    const next = seenAfter(list, prev)
    seenRef.current = next
    writeSeen(next)
    setSeen(next)
  }, [])

  const setPanel = useCallback((next: boolean) => {
    openRef.current = next
    setOpen(next)
  }, [])

  const showList = useCallback(
    (list: NotificationItem[]) => {
      setItems(list)
      if (openRef.current) markSeen(list)
    },
    [markSeen],
  )

  // Set once a read is refused: no more reads or pushed lists.
  const hiddenRef = useRef(false)
  const stream = useEventStream((type, data) => {
    if (type !== 'notifications' || hiddenRef.current) return
    const r = data as Partial<NotificationList> | null
    if (Array.isArray(r?.items)) showList(r.items)
  })
  const polling = stream.mode === 'poll'

  useEffect(() => {
    let live = true
    const load = () => {
      if (hiddenRef.current) return
      listNotifications().then(
        (r) => {
          if (!live) return
          showList(Array.isArray(r?.items) ? r.items : [])
        },
        (e: unknown) => {
          if (!live) return
          if (e instanceof ApiError && HIDE_ON.includes(e.status)) {
            hiddenRef.current = true
            setHidden(true)
          }
        },
      )
    }
    load()
    const timer = polling
      ? window.setInterval(() => {
          if (document.visibilityState !== 'hidden') load()
        }, POLL_MS)
      : undefined
    const onFocus = () => {
      seenRef.current = readSeen() // another tab may have marked them seen
      setSeen(seenRef.current)
      load()
    }
    window.addEventListener('focus', onFocus)
    return () => {
      live = false
      window.clearInterval(timer)
      window.removeEventListener('focus', onFocus)
    }
  }, [showList, polling, stream.syncs])

  // Close on a click elsewhere or Escape, like the account menu.
  useEffect(() => {
    if (!open) return
    const close = (e: Event) => {
      if (e instanceof KeyboardEvent) {
        if (e.key !== 'Escape') return
        setPanel(false)
        button.current?.focus()
      } else if (!wrap.current?.contains(e.target as Node)) setPanel(false)
    }
    document.addEventListener('pointerdown', close)
    document.addEventListener('keydown', close)
    return () => {
      document.removeEventListener('pointerdown', close)
      document.removeEventListener('keydown', close)
    }
  }, [open, setPanel])

  if (hidden) return null
  const count = unreadCount(items, seen)

  const toggle = () => {
    if (open) return setPanel(false)
    setFresh(new Set())
    setAlignStart(panelFitsLeftwards(button.current) === false)
    setPanel(true)
    markSeen(items)
  }

  return (
    <div className="notify-bell" ref={wrap}>
      <button
        ref={button}
        type="button"
        className="notify-bell-btn"
        aria-label={bellLabel(count)}
        aria-expanded={open}
        aria-controls={panelId}
        onClick={toggle}
      >
        <svg aria-hidden="true" viewBox="0 0 24 24" width="18" height="18" focusable="false">
          <path
            fill="currentColor"
            d="M12 22a2.5 2.5 0 0 0 2.45-2h-4.9A2.5 2.5 0 0 0 12 22zm7-6v-5a7 7 0 0 0-5.5-6.84V3.5a1.5 1.5 0 0 0-3 0v.66A7 7 0 0 0 5 11v5l-2 2v1h18v-1z"
          />
        </svg>
        {count > 0 && (
          <span className="notify-count" aria-hidden="true" data-testid="notify-count">
            <Badge tone="accent">{count}</Badge>
          </span>
        )}
      </button>
      {open && (
        <div className={alignStart ? 'notify-panel align-start' : 'notify-panel'} id={panelId} role="region" aria-labelledby={`${panelId}-title`}>
          <p className="notify-panel-title" id={`${panelId}-title`}>
            Recent notifications
          </p>
          {items.length === 0 ? (
            <p className="muted notify-empty">{EMPTY_TEXT}</p>
          ) : (
            <ul className="notify-list">
              {items.map((i) => {
                const b = kindBadge(i.kind)
                const isNew = fresh.has(i.id)
                return (
                  <li key={i.id} className={isNew ? 'notify-item is-new' : 'notify-item'}>
                    <div className="notify-item-meta">
                      <Badge tone={b.tone}>{b.label}</Badge>
                      <time dateTime={isoTime(i.at)}>{shortAgo(i.at)}</time>
                      {isNew && <span className="notify-new">New</span>}
                    </div>
                    <p className="notify-text">{i.text}</p>
                  </li>
                )
              })}
            </ul>
          )}
          <p className="notify-note">{KEPT_NOTE}</p>
        </div>
      )}
    </div>
  )
}
