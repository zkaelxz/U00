/** The lines of a session in a scroll box that fills the height it is given, newest first. */
import { useEffect, useRef, useState } from 'react'

import { fmtTs } from '../../api/live'
import type { LiveCue } from '../../types/live'
import { canSpeak, newestEnglish, speakLine, spokenStep, waitsForATap } from './newestLine'
import type { TextSize } from './ViewingOptions'

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

function useSpokenNewest(newest: LiveCue | null, on: boolean): boolean {
  const said = useRef<number | null>(null)
  const [tapNeeded, setTapNeeded] = useState(() => waitsForATap())
  useEffect(() => {
    if (!on || !tapNeeded) return undefined
    const tapped = () => setTapNeeded(false)
    window.addEventListener('pointerdown', tapped, { once: true })
    window.addEventListener('keydown', tapped, { once: true })
    return () => {
      window.removeEventListener('pointerdown', tapped)
      window.removeEventListener('keydown', tapped)
    }
  }, [on, tapNeeded])
  useEffect(() => {
    if (!canSpeak()) return
    const step = spokenStep(said.current, newest, on)
    said.current = step.said
    if (step.text && !tapNeeded) speakLine(window.speechSynthesis, step.text)
    if (!on) window.speechSynthesis.cancel()
  }, [newest, on, tapNeeded])
  useEffect(() => () => { if (canSpeak()) window.speechSynthesis.cancel() }, [])
  return on && tapNeeded && canSpeak()
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
export function LinesPane({ cues, total, lang, waiting, size = 'm', tvSize, showOriginal = true, speak = false }: { cues: LiveCue[]; total: number; lang: string; waiting: boolean; size?: TextSize; tvSize?: TextSize; showOriginal?: boolean; speak?: boolean }) {
  const box = useRef<HTMLDivElement>(null)
  const newest = newestEnglish(cues)
  const tapToSpeak = useSpokenNewest(newest, speak)
  // The line count when the reader scrolled away from the newest; null while the newest is in view.
  const [awayAt, setAwayAt] = useState<number | null>(null)
  const fresh = awayAt === null ? 0 : Math.max(0, total - awayAt)
  const onScroll = (top: number) => setAwayAt((was) => (top < 24 ? null : was ?? total))
  return (
    <div className="live-lines" data-size={size} data-tv-size={tvSize} data-original={showOriginal ? undefined : 'off'}>
      <div className="live-cues-box" ref={box} tabIndex={0} aria-label="Live lines" onScroll={(e) => onScroll(e.currentTarget.scrollTop)}>
        <CueFeed cues={cues} lang={lang} waiting={waiting} />
      </div>
      {tapToSpeak && <p className="muted" data-testid="live-speak-paused">Speaking starts once you click or tap the page.</p>}
      {/* Holds only the newest English, so a screen reader says each line once as it arrives. */}
      <p className="visually-hidden" role="log" aria-live="polite" data-testid="live-newest">{newest?.translated ?? ''}</p>
      {fresh > 0 && (
        <button type="button" className="live-new-lines" data-testid="live-new-lines" onClick={() => box.current?.scrollTo({ top: 0, behavior: 'smooth' })}>
          {fresh} new {fresh === 1 ? 'line' : 'lines'}
        </button>
      )}
    </div>
  )
}
