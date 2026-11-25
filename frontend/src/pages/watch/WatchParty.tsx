/** The Watch page's watch party: this account's phones play the title in step. Play, pause and seeking on one are done on all. */
import { useCallback, useEffect, useRef, useState, type RefObject } from 'react'

import { ApiError } from '../../api/client'
import { followWatchParty, watchPartyApi, type WatchPartyRoom } from '../../api/watchParty'
import { buttonClass } from '../../components/uiClasses'
import { routeHref } from '../../router'
import { clockOffset, movesToMatch } from './watchPartyLogic'

const FAILED = 'The watch party could not be reached. Try again in a moment.'
const ENDED = 'This watch party has ended.'
const NEEDS_TAP = 'Press play to join in.'
// A move made to match the party raises play, pause and seeked on this phone; for this long they are not sent back as
// the person's own, or two phones would answer each other for ever.
const ECHO_MS = 800
// Pause then seeked (or play then seeked) arrive together as one move.
const SEND_DELAY_MS = 120
const CHECK_MS = 2000

export function WatchParty({ dramaId, mediaRef, joinCode }: {
  dramaId: number
  mediaRef: RefObject<HTMLMediaElement | null>
  /** From a shared link: the party to join as soon as the page opens. */
  joinCode?: string
}) {
  const [code, setCode] = useState<string | null>(joinCode ?? null)
  const [status, setStatus] = useState('')
  const [watching, setWatching] = useState(0)
  const room = useRef<WatchPartyRoom | null>(null)
  const offset = useRef(0)
  const quietUntil = useRef(0)

  const match = useCallback(() => {
    const el = mediaRef.current
    const r = room.current
    if (!el || !r) return
    const move = movesToMatch(r, offset.current, Date.now(), el)
    if (!move.seekTo && !move.play && !move.pause) return
    quietUntil.current = Date.now() + ECHO_MS
    if (move.seekTo !== null) el.currentTime = move.seekTo
    if (move.pause) el.pause()
    if (move.play) el.play().then(() => setStatus(''), () => setStatus(NEEDS_TAP)) // a browser may need a tap before it plays
  }, [mediaRef])

  // The party's state arrives over server-sent events.
  useEffect(() => {
    if (!code) return
    let close = () => undefined as void
    const leave = (message: string) => {
      close()
      room.current = null
      setCode(null)
      setWatching(0)
      setStatus(message)
    }
    close = followWatchParty(code, {
      onState: (next) => {
        if (next.drama_id !== dramaId) return leave('This watch party is for another title.')
        room.current = next
        offset.current = clockOffset(next, Date.now())
        setWatching(next.watching)
        match()
      },
      onEnded: () => leave(ENDED),
    })
    return () => close()
  }, [code, dramaId, match])

  // What this person does on their own player goes to the others; a drift left by slow networks is corrected.
  useEffect(() => {
    if (!code) return
    let el: HTMLMediaElement | null = null
    let timer: ReturnType<typeof setTimeout> | null = null
    const send = () => {
      timer = null
      if (!el) return
      watchPartyApi.setState(code, !el.paused, el.currentTime).then(
        (r) => {
          room.current = r
          offset.current = clockOffset(r, Date.now())
        },
        () => setStatus(FAILED),
      )
    }
    const own = () => {
      if (Date.now() < quietUntil.current) return
      if (timer) clearTimeout(timer)
      timer = setTimeout(send, SEND_DELAY_MS)
    }
    const events = ['play', 'pause', 'seeked'] as const
    const attach = () => {
      el = mediaRef.current
      if (el) for (const name of events) el.addEventListener(name, own)
      return el !== null
    }
    // The player may not be on the page yet when the party opens.
    const finder = attach() ? null : setInterval(() => attach() && finder && clearInterval(finder), 500)
    const checker = setInterval(match, CHECK_MS)
    return () => {
      if (finder) clearInterval(finder)
      clearInterval(checker)
      if (timer) clearTimeout(timer)
      if (el) for (const name of events) el.removeEventListener(name, own)
    }
  }, [code, mediaRef, match])

  const start = async () => {
    setStatus('')
    try {
      const made = await watchPartyApi.create(dramaId)
      setCode(made.code)
    } catch (e) {
      setStatus(e instanceof ApiError && e.status === 429 ? e.message : FAILED)
    }
  }

  const endForAll = async () => {
    if (!code) return
    try {
      await watchPartyApi.end(code)
    } catch {
      // already gone: leaving is the same
    }
    room.current = null
    setCode(null)
    setStatus('')
  }

  const link = code ? `${window.location.origin}${window.location.pathname}${routeHref({ name: 'watch', id: dramaId, party: code })}` : ''
  const share = async () => {
    try {
      if (navigator.share) await navigator.share({ title: 'Watch together', url: link })
      else await navigator.clipboard.writeText(link)
      if (!navigator.share) setStatus('Link copied.')
    } catch {
      setStatus('Copy the link from the box.')
    }
  }

  return (
    <section className="card watch-party" aria-label="Watch together">
      <h2 className="card-title">Watch together</h2>
      {code ? (
        <>
          <p role="status">{watching > 1 ? `${watching} phones are watching in step.` : 'Waiting for another phone. Share the link below.'}</p>
          <input className="watch-party-link" readOnly value={link} aria-label="Link to join this watch party" onFocus={(e) => e.currentTarget.select()} />
          <div className="watch-card-actions">
            <button type="button" className={buttonClass()} onClick={() => void share()}>Share link</button>
            <button type="button" className={buttonClass()} onClick={() => setCode(null)}>Leave</button>
            <button type="button" className={buttonClass()} onClick={() => void endForAll()}>End for everyone</button>
          </div>
        </>
      ) : (
        <>
          <p>Play this title on two or more of your own phones, in step. Both must be signed in as you.</p>
          <div className="watch-card-actions">
            <button type="button" className={buttonClass()} onClick={() => void start()}>Start a watch party</button>
          </div>
        </>
      )}
      {status && <p role="status">{status}</p>}
    </section>
  )
}
