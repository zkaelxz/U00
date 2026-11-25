// The Watch page's "Next" card: once an episode is nearly done it has the next title of the series
// translated (a free engine starts by itself, a paid one waits for a tap) and offers it at the end.
import { useCallback, useEffect, useRef, useState, type RefObject } from 'react'

import { getNextEpisode, prepareNextEpisode } from '../../api/nextEpisode'
import { ButtonLink } from '../../components/Button'
import { buttonClass } from '../../components/uiClasses'
import { routeHref } from '../../router'
import type { NextEpisode } from '../../types/nextEpisode'
import { nextCardText, shouldPrepareNext } from './nextLogic'

/** Share of the episode played before the next one is looked at and, on a free engine, queued. */
const AHEAD_AT = 0.75
const POLL_MS = 15000

export function NextCard({ id, boxRef, canStart }: { id: number; boxRef: RefObject<HTMLElement | null>; canStart: boolean }) {
  const [next, setNext] = useState<NextEpisode | null>(null)
  const [near, setNear] = useState(false)
  const [ended, setEnded] = useState(false)
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)
  // One automatic queueing per episode per visit: a failed or removed run is never queued again behind the person's back.
  const autoTried = useRef(false)
  const readyRef = useRef(false)

  useEffect(() => {
    setNext(null)
    setNear(false)
    setEnded(false)
    setFailed(false)
    autoTried.current = false
    readyRef.current = false
    const el = boxRef.current?.querySelector<HTMLMediaElement>('audio, video')
    if (!el) return
    const onTime = () => {
      if (el.duration > 0 && el.currentTime / el.duration >= AHEAD_AT) setNear(true)
    }
    const onEnded = () => {
      setNear(true)
      setEnded(true)
    }
    const onPlay = () => setEnded(false)
    el.addEventListener('timeupdate', onTime)
    el.addEventListener('ended', onEnded)
    el.addEventListener('play', onPlay)
    return () => {
      el.removeEventListener('timeupdate', onTime)
      el.removeEventListener('ended', onEnded)
      el.removeEventListener('play', onPlay)
    }
  }, [id, boxRef])

  const prepare = useCallback(async () => {
    setBusy(true)
    setFailed(false)
    try {
      setNext(await prepareNextEpisode(id))
    } catch {
      setFailed(true)
    } finally {
      setBusy(false)
    }
  }, [id])

  useEffect(() => {
    if (!near) return
    let cancelled = false
    const look = async () => {
      try {
        const got = await getNextEpisode(id)
        if (cancelled) return
        setNext(got)
        readyRef.current = got.state === 'ready'
        if (shouldPrepareNext(got, canStart) && !autoTried.current) {
          autoTried.current = true
          void prepare()
        }
      } catch {
        // A failed look is retried on the next poll; the card stays hidden meanwhile.
      }
    }
    void look()
    const timer = window.setInterval(() => {
      if (!readyRef.current) void look()
    }, POLL_MS)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [near, id, canStart, prepare])

  const label = nextCardText(next, ended)
  if (!next || label === null) return null
  return (
    <section className="card watch-next" aria-label="Next episode">
      <p><strong>Next: {next.title}</strong></p>
      <p role="status">{failed ? 'It could not be queued.' : label}</p>
      <div className="watch-card-actions">
        <ButtonLink variant="primary" href={routeHref({ name: 'watch', id: next.drama_id as number })}>
          {next.state === 'ready' ? 'Watch next' : 'Open next'}
        </ButtonLink>
        {next.state === 'idle' && canStart && (
          <button type="button" className={buttonClass()} disabled={busy} onClick={() => void prepare()}>
            Get it ready
          </button>
        )}
      </div>
    </section>
  )
}
