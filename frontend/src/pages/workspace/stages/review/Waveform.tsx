import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent, type RefObject } from 'react'

import { ApiError } from '../../../../api/client'
import { getPeaks } from '../../../../api/media'
import { buttonClass } from '../../../../components/uiClasses'
import { lineNumber } from '../../../../lineNumber'
import type { ReviewLine } from '../../../../types/review'
import type { PlayerHandle } from './Player'
import { formatTime } from './reviewLogic'
import {
  bucketsFor, clampEdge, clampSpan, DEFAULT_SPAN, edgeBounds, NUDGE_SECONDS, panView, peakColumns, round3, timeToX, viewAround,
  viewForLine, visibleLines, xToTime, zoomView, type View,
} from './waveformLogic'

export type Edge = 'start' | 'end'

interface Props {
  dramaId: number
  lines: ReviewLine[]
  active: ReviewLine | null
  player: RefObject<PlayerHandle | null>
  // Save one edge through the Review edit path; true when saved.
  onRetime: (lineId: number, edge: Edge, value: number) => Promise<boolean>
  // The active line is open in the row editor: its draft would go stale.
  editingActive: boolean
}

const HEIGHT = 96
const PEAK_CACHE = 24
const BUSY_RETRY_MS = 400
const BUSY_RETRIES = 5
const UNAVAILABLE = 'The waveform isn’t available for this audio.'
const EDITING = 'Save or cancel the open edit to drag this line’s edges.'
const RETIME_FAILED = 'Couldn’t move that edge. The line may have changed: reload and try again.'

// The loudness of a window around the active line, with its neighbours as
// cues. Dragging the active line's edges (or arrow keys on a focused edge)
// saves start/end through the same line-edit route as the row editor, and an
// edge stops at its neighbour instead of overlapping it.
export default function Waveform({ dramaId, lines, active, player, onRetime, editingActive }: Props) {
  const box = useRef<HTMLDivElement | null>(null)
  const canvas = useRef<HTMLCanvasElement | null>(null)
  const head = useRef<HTMLDivElement | null>(null)
  const [width, setWidth] = useState(0)
  const [view, setView] = useState<View>(() => viewAround(player.current?.getTime() ?? 0, DEFAULT_SPAN, NaN))
  const [peaks, setPeaks] = useState<number[] | null>(null)
  const [unavailable, setUnavailable] = useState(false)
  const [drag, setDrag] = useState<{ edge: Edge; t: number } | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const cache = useRef(new Map<string, number[]>())
  const busy = useRef(false)
  const duration = () => player.current?.getDuration() ?? NaN

  // Re-centre when another line becomes active, not when its times change.
  const activeId = active?.id ?? null
  const centred = useRef<number | null>(null)
  useEffect(() => {
    if (!active || centred.current === active.id) return
    centred.current = active.id
    setView((v) => viewForLine(active, v.span, duration()))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId])

  useEffect(() => {
    const el = box.current
    if (!el) return
    setWidth(Math.round(el.clientWidth))
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => setWidth(Math.round(el.clientWidth)))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // Peaks for the window; a quick pan or zoom only fetches where it comes to rest.
  useEffect(() => {
    if (width <= 0) return
    const buckets = bucketsFor(width)
    const start = round3(view.start)
    const end = round3(view.start + view.span)
    const key = `${start}:${end}:${buckets}`
    const hit = cache.current.get(key)
    if (hit) {
      setPeaks(hit)
      setUnavailable(false)
      return
    }
    const ctl = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    // A 429 means the server is still on an earlier window: keep what is
    // drawn and ask again shortly instead of showing an error.
    const fetchWindow = (retries: number) => {
      getPeaks(dramaId, start, end, buckets, ctl.signal).then(
        (r) => {
          if (ctl.signal.aborted) return
          if (cache.current.size >= PEAK_CACHE) cache.current.delete(cache.current.keys().next().value as string)
          cache.current.set(key, r.peaks)
          setPeaks(r.peaks)
          setUnavailable(false)
        },
        (e) => {
          if (ctl.signal.aborted) return
          if (e instanceof ApiError && e.status === 429 && retries > 0) {
            timer = setTimeout(() => fetchWindow(retries - 1), BUSY_RETRY_MS)
            return
          }
          // A decode can outlive the retry budget (aborting a request doesn't
          // stop it on the server), so a spent 429 budget is not a failure.
          if (e instanceof ApiError && e.status === 429) return
          setPeaks(null)
          setUnavailable(true)
        },
      )
    }
    timer = setTimeout(() => fetchWindow(BUSY_RETRIES), 150)
    return () => {
      ctl.abort()
      clearTimeout(timer)
    }
  }, [dramaId, view, width])

  useEffect(() => {
    const c = canvas.current
    if (!c || width <= 0) return
    const dpr = window.devicePixelRatio || 1
    c.width = Math.round(width * dpr)
    c.height = Math.round(HEIGHT * dpr)
    const ctx = c.getContext('2d')
    if (!ctx) return
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, width, HEIGHT)
    const style = getComputedStyle(c)
    ctx.fillStyle = style.getPropertyValue('--muted').trim() || '#888'
    const cols = peakColumns(peaks ?? [], width)
    for (let x = 0; x < width; x += 1) {
      const h = Math.max(1, cols[x] * (HEIGHT - 4))
      ctx.fillRect(x, (HEIGHT - h) / 2, 1, h)
    }
  }, [peaks, width])

  // The playhead is moved straight on its element every frame: re-rendering
  // the lines panel four times a second just to slide a line would be heavy.
  useEffect(() => {
    let raf = 0
    const tick = () => {
      const el = head.current
      const t = player.current?.getTime()
      if (el && t !== undefined && width > 0) {
        const x = timeToX(t, view, width)
        el.style.display = x >= 0 && x <= width ? 'block' : 'none'
        el.style.transform = `translateX(${x}px)`
      }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [player, view, width])

  const bounds = useMemo(() => (active ? edgeBounds(lines, active) : null), [lines, active])
  const shown = visibleLines(lines, view)
  const timeAt = (clientX: number) => {
    const r = box.current?.getBoundingClientRect()
    return r && r.width > 0 ? xToTime(clientX - r.left, view, r.width) : 0
  }

  // Held-down arrow keys outrun the save round trip: the newest value waits
  // for the one in flight, and `held` is where the next press builds on.
  const wanted = useRef<{ id: number; edge: Edge; value: number } | null>(null)
  const held = useRef<Partial<Record<Edge, number>>>({})
  // A save queued for the previous line would go out under its id and be refused.
  useEffect(() => {
    wanted.current = null
    held.current = {}
  }, [activeId])
  const commit = async (edge: Edge, value: number) => {
    if (!active) return
    held.current[edge] = value
    wanted.current = { id: active.id, edge, value }
    if (busy.current) return
    busy.current = true
    setMessage(null)
    let ok = true
    while (wanted.current && ok) {
      const w = wanted.current
      wanted.current = null
      // The id is captured with the edit: `active` here is from the render that started the loop.
      ok = await onRetime(w.id, w.edge, w.value)
    }
    busy.current = false
    held.current = {}
    setDrag(null)
    if (!ok) setMessage(RETIME_FAILED)
  }

  const onDown = (edge: Edge) => (e: PointerEvent<HTMLDivElement>) => {
    if (!active || editingActive) {
      if (editingActive) setMessage(EDITING)
      return
    }
    e.stopPropagation()
    e.currentTarget.setPointerCapture?.(e.pointerId)
    setMessage(null)
    setDrag({ edge, t: active[edge] })
  }
  const onMove = (edge: Edge) => (e: PointerEvent<HTMLDivElement>) => {
    if (!drag || drag.edge !== edge || !bounds) return
    setDrag({ edge, t: clampEdge(timeAt(e.clientX), bounds[edge]) })
  }
  const onUp = (edge: Edge) => (e: PointerEvent<HTMLDivElement>) => {
    if (!drag || drag.edge !== edge || !active || !bounds) return
    e.currentTarget.releasePointerCapture?.(e.pointerId)
    const t = clampEdge(timeAt(e.clientX), bounds[edge])
    if (Math.abs(t - active[edge]) < 0.005) setDrag(null)
    else {
      setDrag({ edge, t })
      void commit(edge, t)
    }
  }
  const onKey = (edge: Edge) => (e: KeyboardEvent<HTMLDivElement>) => {
    if (!active || !bounds) return
    if (e.key === 'Escape' && drag) {
      setDrag(null)
      return
    }
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
    e.preventDefault()
    if (editingActive) {
      setMessage(EDITING)
      return
    }
    const step = (e.shiftKey ? 10 : 1) * NUDGE_SECONDS * (e.key === 'ArrowLeft' ? -1 : 1)
    const base = held.current[edge] ?? active[edge]
    const t = clampEdge(base + step, bounds[edge])
    if (t !== base) void commit(edge, t)
  }

  const seek = (e: PointerEvent<HTMLDivElement>) => player.current?.seek(timeAt(e.clientX))
  const dur = duration()
  const zoom = (f: number) => setView((v) => zoomView(v, f, duration()))
  const pan = (dir: number) => setView((v) => panView(v, (dir * v.span) / 2, duration()))

  const edgeTime = (edge: Edge) => (drag?.edge === edge ? drag.t : active ? active[edge] : 0)
  const shownStart = active ? edgeTime('start') : 0
  const shownEnd = active ? edgeTime('end') : 0

  return (
    <div className="review-wave" role="group" aria-label="Waveform">
      <div className="review-wave-tools">
        <button type="button" className={buttonClass('ghost', 'sm')} aria-label="Earlier" onClick={() => pan(-1)}>‹</button>
        <button type="button" className={buttonClass('ghost', 'sm')} aria-label="Later" onClick={() => pan(1)}>›</button>
        <button type="button" className={buttonClass('ghost', 'sm')} aria-label="Zoom out" disabled={view.span >= clampSpan(Infinity)} onClick={() => zoom(2)}>−</button>
        <button type="button" className={buttonClass('ghost', 'sm')} aria-label="Zoom in" disabled={view.span <= clampSpan(0)} onClick={() => zoom(0.5)}>+</button>
        {active && (
          <button type="button" className={buttonClass('ghost', 'sm')} onClick={() => setView(viewForLine(active, view.span, dur))}>
            Centre on line #{lineNumber(active.idx)}
          </button>
        )}
        <span className="muted review-wave-range" data-testid="wave-range">
          {formatTime(view.start)} – {formatTime(view.start + view.span)}
        </span>
      </div>
      <div className="review-wave-box" ref={box} style={{ height: HEIGHT }} onPointerDown={seek} data-testid="wave-box">
        <canvas ref={canvas} className="review-wave-canvas" style={{ width: '100%', height: HEIGHT }} aria-hidden="true" />
        {shown.map((l) => {
          const isActive = l.id === activeId
          const s = isActive ? shownStart : l.start
          const e = isActive ? shownEnd : l.end
          return (
            <div
              key={l.id}
              className={isActive ? 'review-wave-cue is-active' : 'review-wave-cue'}
              data-testid={isActive ? 'wave-cue-active' : 'wave-cue'}
              style={{ left: timeToX(s, view, width), width: Math.max(1, timeToX(e, view, width) - timeToX(s, view, width)) }}
            >
              <span className="review-wave-cue-label">#{lineNumber(l.idx)}</span>
            </div>
          )
        })}
        {active && bounds && (['start', 'end'] as const).map((edge) => {
          const t = edgeTime(edge)
          return (
            <div
              key={edge}
              role="slider"
              tabIndex={0}
              className={drag?.edge === edge ? 'review-wave-handle is-dragging' : 'review-wave-handle'}
              data-testid={`wave-handle-${edge}`}
              aria-label={`Line #${lineNumber(active.idx)} ${edge}`}
              aria-orientation="horizontal"
              aria-valuemin={round3(bounds[edge].min)}
              aria-valuemax={round3(bounds[edge].max)}
              aria-valuenow={t}
              aria-valuetext={formatTime(t)}
              style={{ left: timeToX(t, view, width) }}
              onPointerDown={onDown(edge)}
              onPointerMove={onMove(edge)}
              onPointerUp={onUp(edge)}
              onPointerCancel={() => setDrag(null)}
              onKeyDown={onKey(edge)}
            />
          )
        })}
        <div className="review-wave-head" ref={head} data-testid="wave-playhead" />
        {unavailable && <p className="review-wave-note muted">{UNAVAILABLE}</p>}
      </div>
      {message && (
        <p className="error" role="alert">
          {message}
        </p>
      )}
    </div>
  )
}
