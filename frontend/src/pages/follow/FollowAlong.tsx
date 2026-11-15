// Translated lines on their own clock, for listening to the audio in another app (for example Fanjiao).
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { api } from '../../api/client'
import { ErrorBanner } from '../../components/ErrorBanner'
import { PageShell } from '../../components/PageShell'
import { usePersistedState } from '../../hooks/usePersistedState'
import { scrollBehavior } from '../../hooks/useMediaQuery'
import { routeHref } from '../../router'
import { activeIndex, alongLines } from '../reader/readAlongLogic'
import { listAllLines } from '../../api/review'
import type { ReviewLine } from '../../types/review'
import '../reader/reader.css'
import {
  formatClock, nudge, pause, positionAt, seekTo, start, STOPPED_AT_START, type FollowClock,
} from './followLogic'

// A phone that sleeps mid-episode stops the clock from being watched, so ask the browser to keep the screen on.
// Browsers without the API, or that refuse it, just let the screen sleep.
function useScreenAwake(on: boolean) {
  useEffect(() => {
    if (!on || !('wakeLock' in navigator)) return
    let lock: WakeLockSentinel | null = null
    let cancelled = false
    navigator.wakeLock.request('screen').then(
      (l) => (cancelled ? void l.release() : (lock = l)),
      () => {},
    )
    return () => {
      cancelled = true
      void lock?.release()
    }
  }, [on])
}

export default function FollowAlongPage({ id }: { id: number }) {
  const [title, setTitle] = useState<string | null>(null)
  const [rows, setRows] = useState<ReviewLine[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [show, setShow] = usePersistedState<string>('follow.text', 'translation')
  const [clock, setClock] = useState<FollowClock>(STOPPED_AT_START)
  const [now, setNow] = useState(() => Date.now())
  const active = useRef<HTMLLIElement | null>(null)

  useEffect(() => {
    let cancelled = false
    api.getDrama(id).then(
      (d) => !cancelled && setTitle(d.title_en || d.title_zh || `Title #${d.id}`),
      () => {},
    )
    listAllLines(id).then(
      (r) => !cancelled && setRows(r),
      () => !cancelled && setError('The lines could not be loaded.'),
    )
    return () => {
      cancelled = true
    }
  }, [id])

  // Only tick while running; a paused clock needs no redraws.
  useEffect(() => {
    if (!clock.running) return
    const t = window.setInterval(() => setNow(Date.now()), 200)
    return () => window.clearInterval(t)
  }, [clock.running])
  useScreenAwake(clock.running)

  const lines = useMemo(() => (rows ? alongLines(rows, show === 'original' ? 'original' : 'translation') : null), [rows, show])
  const position = positionAt(clock, now)
  const current = lines ? activeIndex(lines, position) : null
  useEffect(() => {
    active.current?.scrollIntoView({ block: 'center', behavior: scrollBehavior() })
  }, [current])

  const act = useCallback((f: (c: FollowClock, t: number) => FollowClock) => {
    const t = Date.now()
    setNow(t)
    setClock((c) => f(c, t))
  }, [])

  return (
    <PageShell
      width="narrow"
      title={title ? `Follow along: ${title}` : 'Follow along'}
      meta="Start this clock at the same moment you press play in the other app. Nudge it if the lines drift."
      back={<a className="muted" href={routeHref({ name: 'drama', id, stage: null })}>‹ Back to the title</a>}
    >
      {error && <ErrorBanner error={error} />}
      <div className="stack">
        <div className="row" role="group" aria-label="Clock">
          <button type="button" className="btn" onClick={() => act((c, t) => (c.running ? pause(c, t) : start(c, t)))}>
            {clock.running ? 'Pause' : 'Start'}
          </button>
          <button type="button" className="btn btn-secondary" onClick={() => act((c, t) => nudge(c, -5, t))}>−5 s</button>
          <button type="button" className="btn btn-secondary" onClick={() => act((c, t) => nudge(c, -0.5, t))}>−0.5 s</button>
          <button type="button" className="btn btn-secondary" onClick={() => act((c, t) => nudge(c, 0.5, t))}>+0.5 s</button>
          <button type="button" className="btn btn-secondary" onClick={() => act((c, t) => nudge(c, 5, t))}>+5 s</button>
          <span aria-live="off" data-testid="follow-clock">{formatClock(position)}</span>
        </div>
        <label className="field">
          <span>Show</span>
          <select value={show === 'original' ? 'original' : 'translation'} onChange={(e) => setShow(e.target.value)}>
            <option value="translation">English</option>
            <option value="original">Original</option>
          </select>
        </label>
        {lines && lines.length === 0 && <p className="muted">This title has no {show === 'original' ? 'source' : 'translated'} lines yet.</p>}
        {lines && lines.length > 0 && (
          <ol className="read-along" aria-label="Lines">
            {lines.map((l, i) => (
              <li key={l.id} ref={i === current ? active : undefined} className={i === current ? 'is-current' : undefined} aria-current={i === current ? 'true' : undefined}>
                <button type="button" onClick={() => act((c, t) => seekTo(c, l.start, t))}>{l.text}</button>
              </li>
            ))}
          </ol>
        )}
      </div>
    </PageShell>
  )
}
