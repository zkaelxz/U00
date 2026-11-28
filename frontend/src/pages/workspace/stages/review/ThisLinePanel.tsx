/** The check queue's This line panel beside the cards: the open line's sound, why it is listed, and the lines around it. */
import { useEffect, useId, useRef, useState, type RefObject } from 'react'
import { createPortal } from 'react-dom'

import { getPeaks } from '../../../../api/media'
import { listAllLines, listLines } from '../../../../api/review'
import { buttonClass } from '../../../../components/uiClasses'
import { chineseLang } from '../../../../hanScript'
import { lineNumber } from '../../../../lineNumber'
import { routeHref } from '../../../../router'
import type { ReviewLine, ReviewLinesPage } from '../../../../types/review'
import { alsoUnsureText, aroundFrom, queueAction, queueReason, type Around } from './checkQueueModel'
import { ReasonDetails } from './LineRow'
import type { PlayerHandle } from './Player'
import { sourceTextLang } from './reviewDraft'
import { formatTenths, PAGE_SIZE, pageForPosition } from './reviewLogic'
import { useTitleTextLang } from '../../StageContext'

// Half a second either side shows where the speech starts and stops around the line.
const SOUND_PAD_S = 0.5
// 4px of the 338px the sound card's picture has in the tools column: a bar and the gap after it.
const SOUND_BARS = 84

interface Props {
  dramaId: number
  line: ReviewLine
  // An audio title: the sound card is shown (a video's own card already shows its picture and sound).
  sound: boolean
  player: RefObject<PlayerHandle | null>
  // Bumped after every write: the lines around are read again, never shown from before an edit.
  reloads: number
  sourceLanguage: string | null
  onShowLine: (id: number) => void
}

export function ThisLinePanel({ dramaId, line, sound, player, reloads, sourceLanguage, onShowLine }: Props) {
  const around = useLinesAround(dramaId, line, reloads)
  return (
    <div className="this-line" data-testid="this-line">
      {sound && <LineSound dramaId={dramaId} line={line} player={player} />}
      <LineWhy dramaId={dramaId} line={line} />
      <LinesAround line={line} around={around} sourceLanguage={sourceLanguage} onShowLine={onShowLine} />
    </div>
  )
}

// The panel drawn into the tools' This line tab, which another part of the stage owns.
export function ThisLineDock({ dock, ...props }: Props & { dock: HTMLElement }) {
  return createPortal(<ThisLinePanel {...props} />, dock)
}

interface Peaks {
  key: string
  from: number
  to: number
  peaks: number[]
}

export function LineSound({ dramaId, line, player }: { dramaId: number; line: ReviewLine; player: RefObject<PlayerHandle | null> }) {
  const headId = useId()
  const key = `${line.id}:${line.start}:${line.end}`
  const [got, setGot] = useState<Peaks | null>(null)
  useEffect(() => {
    const duration = player.current?.getDuration() ?? NaN
    const from = Math.max(0, line.start - SOUND_PAD_S)
    const to = Number.isFinite(duration) && duration > 0 ? Math.min(duration, line.end + SOUND_PAD_S) : line.end + SOUND_PAD_S
    if (!(to > from)) return
    const ask = new AbortController()
    getPeaks(dramaId, from, to, SOUND_BARS, ask.signal).then(
      (r) => setGot({ key, from: r.start ?? from, to: r.end ?? to, peaks: r.peaks ?? [] }),
      // No picture is the whole answer to a failed request: the times and Play still work.
      () => undefined,
    )
    return () => ask.abort()
    // The key carries the line's id and times.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dramaId, key])
  const picture = got && got.key === key && got.peaks.length > 0 ? got : null
  return (
    <section className="card this-line-card" aria-labelledby={headId}>
      <div className="this-line-head">
        <h3 id={headId} className="this-line-title">Line {lineNumber(line.idx)} sound</h3>
        <button type="button" className={buttonClass('secondary', 'sm')} title="Key: Space" onClick={() => player.current?.toggleLine(line)}>
          Play
        </button>
      </div>
      {picture && <SoundPicture picture={picture} line={line} player={player} />}
      <div className="this-line-times">
        <span className="review-time"><span className="sr-only">Start </span>{formatTenths(line.start)}</span>
        <span className="review-time"><span className="sr-only">End </span>{formatTenths(line.end)}</span>
      </div>
    </section>
  )
}

function SoundPicture({ picture, line, player }: { picture: Peaks; line: ReviewLine; player: RefObject<PlayerHandle | null> }) {
  const head = useRef<HTMLDivElement>(null)
  const { from, to, peaks } = picture
  // The playhead is moved straight on the element each frame, so playing never re-renders the panel. The frame loop
  // runs only while the player plays, and writes a style only when its value changes.
  useEffect(() => {
    const p = player.current
    if (!p) return
    let frame = 0
    let visibility = 'hidden'
    let left = ''
    const place = (t: number | null) => {
      const el = head.current
      if (!el) return
      const nextVisibility = t === null ? 'hidden' : 'visible'
      if (nextVisibility !== visibility) {
        visibility = nextVisibility
        el.style.visibility = visibility
      }
      const nextLeft = t === null ? left : `${((t - from) / (to - from)) * 100}%`
      if (nextLeft !== left) {
        left = nextLeft
        el.style.left = left
      }
    }
    const tick = () => {
      frame = 0
      const t = p.getTime()
      const playing = p.isPlaying()
      place(playing && t >= from && t <= to ? t : null)
      if (playing) frame = requestAnimationFrame(tick)
    }
    const wake = () => {
      if (!frame) frame = requestAnimationFrame(tick)
    }
    const off = p.onPlayChange(wake)
    wake()
    return () => {
      off()
      cancelAnimationFrame(frame)
    }
  }, [from, to, player])
  const step = (to - from) / peaks.length
  return (
    <div className="this-line-wave" aria-hidden="true" data-testid="this-line-wave">
      <svg viewBox={`0 0 ${peaks.length * 4} 100`} preserveAspectRatio="none">
        {peaks.map((p, i) => {
          const at = from + (i + 0.5) * step
          const h = Math.max(4, (Math.min(255, Math.max(0, p)) / 255) * 100)
          return <rect key={i} x={i * 4} width={3} rx={1.5} ry={2.8} y={(100 - h) / 2} height={h} className={at >= line.start && at <= line.end ? 'is-in' : undefined} />
        })}
      </svg>
      <div ref={head} className="this-line-playhead" style={{ visibility: 'hidden' }} />
    </div>
  )
}

export function LineWhy({ dramaId, line }: { dramaId: number; line: ReviewLine }) {
  const headId = useId()
  const reason = queueReason(line)
  if (!reason) return null
  const also = alsoUnsureText(line)
  return (
    <section className="card this-line-card" aria-labelledby={headId}>
      <h3 id={headId} className="this-line-title">Why it is listed</h3>
      <p className="this-line-why" data-testid="this-line-why">{also ? `${reason.text} ${also}` : reason.text}</p>
      <ReasonDetails details={reason.details} />
      {queueAction(line) === 'glossary' && (
        <a className="this-line-link" href={routeHref({ name: 'drama', id: dramaId, stage: 'translate', tab: 'glossary' })}>Glossary</a>
      )}
    </section>
  )
}

function NearText({ line, sourceLanguage }: { line: ReviewLine; sourceLanguage: string | null }) {
  const titleLang = useTitleTextLang()
  return (
    <>
      <span className="this-line-near-zh">
        <span className="this-line-near-idx">#{lineNumber(line.idx)}</span> <span lang={chineseLang(sourceTextLang(line.lang, sourceLanguage), line.zh, titleLang)}>{line.zh}</span>
      </span>
      <span className="this-line-near-en">{line.en || <span className="muted review-untranslated">Not translated</span>}</span>
    </>
  )
}

export function NearLine({ line, sourceLanguage, onShowLine }: { line: ReviewLine; sourceLanguage: string | null; onShowLine: (id: number) => void }) {
  return (
    <button type="button" className="this-line-near" title="Open in full list" onClick={() => onShowLine(line.id)}>
      <NearText line={line} sourceLanguage={sourceLanguage} />
    </button>
  )
}

export function LinesAround({ line, around, sourceLanguage, onShowLine }: {
  line: ReviewLine
  around: Around | null
  sourceLanguage: string | null
  onShowLine: (id: number) => void
}) {
  const headId = useId()
  return (
    <section className="card this-line-card" aria-labelledby={headId}>
      <h3 id={headId} className="this-line-title">Around it</h3>
      <ol className="this-line-around" data-testid="this-line-around">
        {around?.before && <li><NearLine line={around.before} sourceLanguage={sourceLanguage} onShowLine={onShowLine} /></li>}
        <li><div className="this-line-near is-open" aria-current="true"><NearText line={line} sourceLanguage={sourceLanguage} /></div></li>
        {around?.after && <li><NearLine line={around.after} sourceLanguage={sourceLanguage} onShowLine={onShowLine} /></li>}
      </ol>
    </section>
  )
}

// The queue's own page holds only lines that need a look, so the neighbours come from the All view. Every write
// renumbers idx from 0 in line order, so a line's idx is its place there; a line not found where its idx says
// (numbering with a gap) falls back to reading every line once.
function useLinesAround(dramaId: number, line: ReviewLine, reloads: number): Around | null {
  const cache = useRef<{ key: string; pages: Map<number, Promise<ReviewLinesPage>> }>({ key: '', pages: new Map() })
  const [got, setGot] = useState<{ key: string; around: Around } | null>(null)
  const want = `${dramaId}:${reloads}:${line.id}:${line.idx}`
  useEffect(() => {
    const cacheKey = `${dramaId}:${reloads}`
    if (cache.current.key !== cacheKey) cache.current = { key: cacheKey, pages: new Map() }
    const pages = cache.current.pages
    const load = (n: number) => {
      let p = pages.get(n)
      if (!p) {
        p = listLines(dramaId, n, PAGE_SIZE, 'all')
        pages.set(n, p)
        p.catch(() => pages.delete(n))
      }
      return p
    }
    let cancelled = false
    const find = async (): Promise<Around> => {
      const n = pageForPosition(line.idx)
      const page = await load(n)
      const at = page.lines.findIndex((l) => l.id === line.id)
      if (at === -1) {
        const all = await listAllLines(dramaId)
        return aroundFrom(all, line.id, null, null) ?? { before: null, after: null }
      }
      const prev = at === 0 && n > 1 ? ((await load(n - 1)).lines.at(-1) ?? null) : null
      const next = at === page.lines.length - 1 && n * PAGE_SIZE < page.total ? ((await load(n + 1)).lines[0] ?? null) : null
      return aroundFrom(page.lines, line.id, prev, next) ?? { before: null, after: null }
    }
    find().then(
      (around) => !cancelled && setGot({ key: want, around }),
      () => !cancelled && setGot({ key: want, around: { before: null, after: null } }),
    )
    return () => {
      cancelled = true
    }
    // `want` carries every input.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [want])
  return got?.key === want ? got.around : null
}
