// The narration player with the lines beside it, the one being spoken highlighted.
import { useEffect, useMemo, useRef, useState } from 'react'

import { listenApi, listenTrackUrl } from '../../api/listen'
import { usePersistedState } from '../../hooks/usePersistedState'
import { scrollBehavior } from '../../hooks/useMediaQuery'
import { listAllLines } from '../../api/review'
import type { ReviewLine } from '../../types/review'
import { activeIndex, alongLines, type AlongLine } from './readAlongLogic'

// version: counts the times the narration was replaced while this page was open.
export function ReadAlong({ dramaId, version, onError }: { dramaId: number; version: number; onError: () => void }) {
  const [loaded, setLoaded] = useState<{ rows: ReviewLine[]; spoken: string } | null>(null)
  // Empty follows the language the voice speaks; Original / English overrides it.
  const [showPref, setShowPref] = usePersistedState<string>('reader.alongText', '')
  const [time, setTime] = useState(0)
  const audio = useRef<HTMLAudioElement | null>(null)
  const active = useRef<HTMLLIElement | null>(null)

  useEffect(() => {
    let cancelled = false
    // The lines are only a read-along, so a failure leaves the plain player.
    Promise.all([listenApi.config(dramaId), listAllLines(dramaId)]).then(
      ([cfg, rows]) => !cancelled && setLoaded({ rows, spoken: cfg.narration_language }),
      () => !cancelled && setLoaded({ rows: [], spoken: '' }),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId, version])

  const shown = showPref === 'original' || showPref === 'translation' ? showPref : (loaded?.spoken ?? '')
  const lines = useMemo<AlongLine[] | null>(() => (loaded ? alongLines(loaded.rows, shown) : null), [loaded, shown])
  const current = useMemo(() => (lines ? activeIndex(lines, time) : null), [lines, time])

  useEffect(() => {
    active.current?.scrollIntoView({ block: 'nearest', behavior: scrollBehavior() })
  }, [current])

  const jump = (start: number) => {
    const el = audio.current
    if (!el) return
    el.currentTime = start
    void el.play().catch(() => {})
  }

  return (
    <div className="stack">
      <span className="muted">Narration</span>
      <audio
        ref={audio}
        controls
        preload="none"
        src={version > 0 ? `${listenTrackUrl(dramaId)}?v=${version}` : listenTrackUrl(dramaId)}
        onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)}
        onError={onError}
      />
      {loaded && loaded.rows.length > 0 && (
        <label className="field">
          <span>Show</span>
          <select value={shown === 'original' ? 'original' : 'translation'} onChange={(e) => setShowPref(e.target.value)}>
            <option value="original">Original</option>
            <option value="translation">English</option>
          </select>
        </label>
      )}
      {lines && lines.length > 0 && (
        <ol className="read-along" aria-label="Lines being narrated">
          {lines.map((l, i) => (
            <li key={l.id} ref={i === current ? active : undefined} className={i === current ? 'is-current' : undefined} aria-current={i === current ? 'true' : undefined}>
              <button type="button" onClick={() => jump(l.start)}>{l.text}</button>
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}
