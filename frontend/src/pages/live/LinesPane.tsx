/** The lines of a session in a scroll box that fills the height it is given, newest first. */
import { useEffect, useRef, useState } from 'react'

import { fmtTs } from '../../api/live'
import type { LiveCue } from '../../types/live'

/** The translation, or why there is none yet: the transcript under it always stays. */
function TranslationText({ cue, waiting }: { cue: LiveCue; waiting: boolean }) {
  if (cue.translation === 'pending' && waiting) {
    return <span className="live-en muted" data-testid="live-translating">translating…</span>
  }
  if (cue.translation === 'skipped') {
    return <span className="live-en muted" data-testid="live-untranslated">Not translated: skipped to keep up with the stream.</span>
  }
  if (cue.translation === 'pending' || cue.translation === 'cancelled') {
    return <span className="live-en muted" data-testid="live-untranslated">Not translated: the session stopped first.</span>
  }
  return <span className={cue.translation === 'failed' ? 'live-en warn' : 'live-en'}>{cue.translated}</span>
}

function CueFeed({ cues, lang, waiting }: { cues: LiveCue[]; lang: string; waiting: boolean }) {
  return (
    <ol className="live-cues" aria-label="Live lines, newest first">
      {cues.map((c) => (
        <li key={c.id}>
          <TranslationText cue={c} waiting={waiting} />
          <span className="live-src" lang={lang}>{c.text}</span>
          <span className="live-ts">{fmtTs(c.start)}</span>
        </li>
      ))}
    </ol>
  )
}

/** `total`: every line so far, which keeps counting after the box stops showing the oldest. */
export function LinesPane({ cues, total, lang, waiting }: { cues: LiveCue[]; total: number; lang: string; waiting: boolean }) {
  const box = useRef<HTMLDivElement>(null)
  // The count when the reader last had the newest line in view.
  const [seen, setSeen] = useState(total)
  const [atTop, setAtTop] = useState(true)
  useEffect(() => {
    if (atTop) setSeen(total)
  }, [atTop, total])
  const fresh = atTop ? 0 : Math.max(0, total - seen)
  return (
    <div className="live-lines">
      <div className="live-cues-box" ref={box} tabIndex={0} aria-label="Live lines" onScroll={(e) => setAtTop(e.currentTarget.scrollTop < 24)}>
        <CueFeed cues={cues} lang={lang} waiting={waiting} />
      </div>
      {fresh > 0 && (
        <button type="button" className="live-new-lines" data-testid="live-new-lines" onClick={() => box.current?.scrollTo({ top: 0, behavior: 'smooth' })}>
          {fresh} new {fresh === 1 ? 'line' : 'lines'}
        </button>
      )}
    </div>
  )
}
