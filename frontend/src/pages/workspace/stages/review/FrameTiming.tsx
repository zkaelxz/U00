// The frame timing view: the picture at a line's first and last frame, with frame-step controls for each edge.
import { useEffect, useRef, useState, type KeyboardEvent } from 'react'

import { analyzeMedia } from '../../../../api/metadata'
import { mediaStreamUrl } from '../../../../api/media'
import { describeError } from '../../../../components/errorMessages'
import { Sheet } from '../../../../components/Sheet'
import { buttonClass } from '../../../../components/uiClasses'
import type { ReviewLine } from '../../../../types/review'
import { FALLBACK_FPS, FRAME_JUMP, frameClock, frameOf, stepFrames } from './frameTimingLogic'
import type { Edge } from './Waveform'
import { neighbourCut, type EdgeBounds } from './waveformLogic'
import './frameTiming.css'

interface Props {
  dramaId: number
  active: ReviewLine
  bounds: { start: EdgeBounds; end: EdgeBounds }
  cuts: number[]
  onCommit: (edge: Edge, value: number) => void
  // Why the last save failed, shown inside the dialog.
  error: string | null
}

export function FrameTimingButton({ dramaId, active, bounds, cuts, onCommit, error }: Props) {
  const [open, setOpen] = useState(false)
  const [fps, setFps] = useState<number | null>(null)
  const [failure, setFailure] = useState<string | null>(null)

  const show = async () => {
    setFailure(null)
    try {
      const a = await analyzeMedia(dramaId)
      if (!a.has_video) throw new Error('no video')
      setFps(a.fps && a.fps > 0 ? a.fps : FALLBACK_FPS)
      setOpen(true)
    } catch (e) {
      setFailure(describeError(e).title)
    }
  }

  return (
    <>
      <button type="button" className={buttonClass('ghost', 'sm')} onClick={() => void show()} data-testid="wave-frame-timing">
        Frame timing
      </button>
      {failure && <span className="error" role="alert">{failure}</span>}
      <Sheet open={open} title="Frame timing" onClose={() => setOpen(false)}>
        {open && fps && <FrameTimingBody dramaId={dramaId} active={active} bounds={bounds} cuts={cuts} fps={fps} onCommit={onCommit} error={error} />}
      </Sheet>
    </>
  )
}

function FrameTimingBody({ dramaId, active, bounds, cuts, fps, onCommit, error }: Props & { fps: number }) {
  const [times, setTimes] = useState({ start: active.start, end: active.end })
  const edge = useRef<Edge>('start')
  const move = (which: Edge, t: number) => {
    if (t === times[which]) return
    setTimes((cur) => ({ ...cur, [which]: t }))
    onCommit(which, t)
  }
  const step = (which: Edge, frames: number) => move(which, stepFrames(times[which], frames, fps, bounds[which]))
  const cut = (which: Edge, dir: 1 | -1) => {
    const c = neighbourCut(cuts, times[which], dir)
    if (c !== null) move(which, stepFrames(c, 0, fps, bounds[which]))
  }

  const onKeyDown = (e: KeyboardEvent) => {
    if (e.ctrlKey || e.metaKey || e.altKey) return
    const n = e.shiftKey ? FRAME_JUMP : 1
    if (e.key === 'ArrowLeft') step(edge.current, -n)
    else if (e.key === 'ArrowRight') step(edge.current, n)
    else if (e.key === ',') cut(edge.current, -1)
    else if (e.key === '.') cut(edge.current, 1)
    else return
    e.preventDefault()
  }

  return (
    <div className="frame-timing" onKeyDown={onKeyDown}>
      {(['start', 'end'] as const).map((which) => (
        <FramePanel
          key={which} which={which} src={mediaStreamUrl(dramaId, 'video')} seconds={times[which]} fps={fps} text={active.en || active.zh}
          onFocus={() => { edge.current = which }} onStep={(f) => step(which, f)}
        />
      ))}
      {error && <p className="error" role="alert">{error}</p>}
    </div>
  )
}

interface PanelProps {
  which: Edge
  src: string
  seconds: number
  fps: number
  text: string
  onFocus: () => void
  onStep: (frames: number) => void
}

function FramePanel({ which, src, seconds, fps, text, onFocus, onStep }: PanelProps) {
  const video = useRef<HTMLVideoElement>(null)
  // A frame boundary can sit a hair before the picture changes; aim at its middle.
  useEffect(() => {
    const el = video.current
    if (el) el.currentTime = seconds + 0.5 / fps
  }, [seconds, fps])
  return (
    <section className="frame-panel" role="group" aria-label={which === 'start' ? 'In' : 'Out'} onFocusCapture={onFocus}>
      <div className="frame-picture">
        <video ref={video} src={src} muted preload="auto" playsInline data-testid={`frame-video-${which}`} />
        <p className="frame-caption">{text}</p>
      </div>
      <div className="frame-controls">
        <span data-testid={`frame-clock-${which}`}>
          {which === 'start' ? 'In' : 'Out'} {frameClock(seconds)} · frame {frameOf(seconds, fps).toLocaleString('en-US')}
        </span>
        <span className="frame-buttons">
          <button type="button" className={buttonClass('secondary', 'sm')} onClick={() => onStep(-1)}>◀ frame</button>
          <button type="button" className={buttonClass('secondary', 'sm')} onClick={() => onStep(1)}>frame ▶</button>
        </span>
      </div>
    </section>
  )
}
