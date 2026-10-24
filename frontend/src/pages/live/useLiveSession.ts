/*
 * The Live session the page shows, and how it follows it: found on load,
 * started here, started from a followed channel, or pushed over GET
 * /api/events. Lines are read from the page's own cursor (pushed status
 * says when there are more; polled every POLL_MS only while that stream is
 * down). Stop captions the audio already captured and then ends; a second
 * Stop ends at once.
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { hasStatus } from '../../api/client'
import {
  POLL_MS, describeLiveError, getLive, hasPending, isActive, listLive, mergeCues, pickSession, readFrom, stopLive,
} from '../../api/live'
import { LIVE_STARTED_EVENT } from '../../api/liveFollows'
import { useEventStream } from '../../hooks/useEventStream'
import { usePersistedState } from '../../hooks/usePersistedState'
import type { LiveCue, LiveSessionStatus } from '../../types/live'

// Consecutive failed reads (about 10 s at POLL_MS) before the page says so.
const POLL_FAILURES_SHOWN = 5

const GONE = 'That live session is gone (Baihe was restarted). Start a new one.'

// `url`: the link this browser started the session with. The status carries no
// link, so a session started elsewhere (another device, a followed channel) has none.
export type Session = { id: string; url: string | null; status: LiveSessionStatus | null; cues: LiveCue[]; next: number }
type StartedLink = { id: string; url: string }

/** Whether a session started elsewhere may replace the shown one: one runs at a time, and a
 *  finished feed someone may still be reading or copying is kept. */
const canReplace = (cur: Session | null) =>
  !cur || (cur.status !== null && !isActive(cur.status.status) && cur.cues.length === 0)

export function useLiveSession(setError: (text: string | null) => void) {
  const [session, setSession] = useState<Session | null>(null)
  // 'finishing': the first Stop was taken and the session captions what it
  // already captured; the button then offers Stop now.
  const [stopping, setStopping] = useState<false | 'finishing' | 'now'>(false)
  // The last session this browser started and its link, so a reload still shows its video.
  const [startedLink, setStartedLink] = usePersistedState<StartedLink | null>('live.startedLink', null)
  const startedLinkRef = useRef(startedLink)
  startedLinkRef.current = startedLink
  const sessionRef = useRef<Session | null>(null)
  const pollFailures = useRef(0)
  const pollErrorShown = useRef(false)
  // Declared before the polling effect so a new session is in the ref first.
  useEffect(() => {
    sessionRef.current = session
  }, [session])

  /** Shows a session this page did not start (found on load, from a followed channel, pushed). */
  const adopt = useCallback((id: string) => {
    const known = startedLinkRef.current
    setSession({ id, url: known?.id === id ? known.url : null, status: null, cues: [], next: 0 })
    setError(null)
  }, [setError])

  // A Stop, or a run of failed reads, belongs to the session it happened to.
  const sessionId = session?.id
  useEffect(() => {
    setStopping(false)
    pollFailures.current = 0
    pollErrorShown.current = false
  }, [sessionId])

  useEffect(() => {
    listLive().then(
      (list) => {
        const id = pickSession(list)
        if (id && !sessionRef.current) adopt(id)
      },
      (err: unknown) => setError(`Could not check for a running live session. ${describeLiveError(err)}`),
    )
    // A session started from a followed channel (its card or the bell).
    const onStarted = (e: Event) => {
      const id = (e as CustomEvent<unknown>).detail
      if (typeof id === 'string') adopt(id)
    }
    window.addEventListener(LIVE_STARTED_EVENT, onStarted)
    return () => window.removeEventListener(LIVE_STARTED_EVENT, onStarted)
  }, [adopt, setError])

  const gone = useCallback(() => {
    setSession(null)
    setError(GONE)
  }, [setError])

  const poll = useCallback(async (id: string) => {
    const cur = sessionRef.current
    if (!cur || cur.id !== id) return false
    // From the oldest line still waiting for its translation, so it fills in.
    const after = readFrom(cur.cues, cur.next)
    try {
      const s = await getLive(id, after)
      pollFailures.current = 0
      if (pollErrorShown.current) {
        pollErrorShown.current = false
        setError(null)
      }
      // Merged by id: a reply that overlaps another changes nothing twice.
      setSession((prev) => {
        if (!prev || prev.id !== id) return prev
        return { ...prev, status: s, cues: mergeCues(prev.cues, s.cues), next: Math.max(prev.next, s.next_index) }
      })
      if (sessionRef.current?.id === id) {
        sessionRef.current = { ...sessionRef.current, next: Math.max(sessionRef.current.next, s.next_index) }
      }
      return isActive(s.status)
    } catch (err) {
      if (hasStatus(err, 404)) {
        gone()
        return false
      }
      // Signed out or not allowed: retrying every 2 s cannot help.
      if (hasStatus(err, 401, 403)) {
        pollErrorShown.current = true
        setError(describeLiveError(err))
        return false
      }
      // A single blip is normal; a run of them means the feed is not coming.
      pollFailures.current += 1
      if (pollFailures.current >= POLL_FAILURES_SHOWN && !pollErrorShown.current) {
        pollErrorShown.current = true
        setError(describeLiveError(err))
      }
      return true
    }
  }, [gone, setError])

  // Pushed status (cues: [] -- the text stays behind the GET): show it, and
  // read the new lines from our cursor when the session has more.
  const stream = useEventStream((type, data) => {
    const s = data as LiveSessionStatus | null
    const cur = sessionRef.current
    if (type !== 'live' || !s) return
    if (!cur || s.session_id !== cur.id) {
      if (canReplace(cur) && isActive(s.status)) adopt(s.session_id)
      return
    }
    setSession((prev) => (prev && prev.id === s.session_id ? { ...prev, status: { ...s, cues: [] } } : prev))
    if (s.next_index > cur.next || hasPending(cur.cues)) void poll(cur.id)
  })
  const polling = stream.mode === 'poll'

  // One read at once and after every (re)connect; the timer only while the
  // stream is down.
  useEffect(() => {
    if (!sessionId) return
    let timer: ReturnType<typeof setTimeout> | undefined
    let alive = true
    const tick = async () => {
      const again = await poll(sessionId)
      if (alive && again && polling) timer = setTimeout(tick, POLL_MS)
    }
    void tick()
    return () => {
      alive = false
      clearTimeout(timer)
    }
  }, [sessionId, poll, polling, stream.syncs])

  /** A session this page just started with `url`. */
  const began = (id: string, url: string) => {
    setStopping(false)
    setStartedLink({ id, url })
    setSession({ id, url, status: null, cues: [], next: 0 })
  }

  /** After a Start refused because one is running: show that one, so its Stop is on screen.
   *  Start only runs with nothing active, so a finished session can be the one shown. */
  const adoptRunning = (message: string) => {
    const shown = sessionRef.current
    listLive().then((list) => {
      const id = list.find((x) => isActive(x.status))?.session_id
      if (id && sessionRef.current === shown) {
        adopt(id)
        setError(message)
      }
    }, () => {})
  }

  async function stop() {
    if (!session) return
    const again = stopping === 'finishing'
    setStopping('now')
    setError(null)
    try {
      const r = await stopLive(session.id)
      if (r.finishing && !again) setStopping('finishing')
      await poll(session.id)
    } catch (err) {
      setStopping(false)
      if (hasStatus(err, 404)) gone()
      else setError(describeLiveError(err))
    }
  }

  return { session, sessionId, stopping, began, stop, adoptRunning }
}
