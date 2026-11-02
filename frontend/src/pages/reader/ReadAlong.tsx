// The narration player with the lines beside it, the one being spoken highlighted.
import { useEffect, useMemo, useRef, useState } from 'react'

import { listenApi, listenTrackUrl } from '../../api/listen'
import { listLines } from '../../api/review'
import type { ReviewLine } from '../../types/review'
import { activeIndex, alongLines, type AlongLine } from './readAlongLogic'

const PAGE_SIZE = 200 // the server's page-size cap

// Every line of the title, in order, paged because the server caps a page.
async function loadLines(dramaId: number, language: string): Promise<AlongLine[]> {
  const rows: ReviewLine[] = []
  for (let page = 1; ; page++) {
    const r = await listLines(dramaId, page, PAGE_SIZE, 'all')
    rows.push(...r.lines)
    if (rows.length >= r.total || r.lines.length === 0) break
  }
  return alongLines(rows, language)
}

export function ReadAlong({ dramaId, onError }: { dramaId: number; onError: () => void }) {
  const [lines, setLines] = useState<AlongLine[] | null>(null)
  const [time, setTime] = useState(0)
  const audio = useRef<HTMLAudioElement | null>(null)
  const active = useRef<HTMLLIElement | null>(null)

  useEffect(() => {
    let cancelled = false
    // The language the voice speaks decides which text is shown; the lines are only a read-along,
    // so a failure leaves the plain player.
    listenApi
      .config(dramaId)
      .then((cfg) => loadLines(dramaId, cfg.narration_language))
      .then((l) => !cancelled && setLines(l), () => !cancelled && setLines([]))
    return () => {
      cancelled = true
    }
  }, [dramaId])

  const current = useMemo(() => (lines ? activeIndex(lines, time) : null), [lines, time])

  useEffect(() => {
    active.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
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
        src={listenTrackUrl(dramaId)}
        onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)}
        onError={onError}
      />
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
