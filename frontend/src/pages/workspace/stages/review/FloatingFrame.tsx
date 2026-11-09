import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

import { buttonClass } from '../../../../components/uiClasses'
import { clampFloatPos, clampFloatWidth, FLOAT_MARGIN, FLOAT_MIN_WIDTH, FLOAT_STEP, type FloatPos } from './floatLogic'

interface Props {
  width: number
  onWidth: (width: number) => void
  onReturn: () => void
  // Gets the element the player panel is moved into (null once the frame closes).
  hostRef: (el: HTMLElement | null) => void
  children?: ReactNode
}

// An in-page floating box for browsers without Document Picture-in-Picture (Firefox, Safari, phones,
// pages not served over HTTPS or localhost). Dragging and resizing have button and key equivalents.
export function FloatingFrame({ width, onWidth, onReturn, hostRef }: Props) {
  const box = useRef<HTMLDivElement | null>(null)
  const [pos, setPos] = useState<FloatPos>({ right: FLOAT_MARGIN, bottom: FLOAT_MARGIN })
  const [viewportWidth, setViewportWidth] = useState(() => window.innerWidth)
  const w = clampFloatWidth(width, viewportWidth)

  const keepOnScreen = (p: FloatPos): FloatPos => {
    const el = box.current
    return el ? clampFloatPos(p, { width: el.offsetWidth, height: el.offsetHeight }, { width: window.innerWidth, height: window.innerHeight }) : p
  }

  // The window can shrink (or a phone turn) under a box placed for the old size.
  useEffect(() => {
    const onResize = () => {
      setViewportWidth(window.innerWidth)
      setPos((p) => keepOnScreen(p))
    }
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  const drag = (e: ReactPointerEvent<HTMLElement>, onMove: (dx: number, dy: number, start: { pos: FloatPos; width: number }) => void) => {
    if (e.button !== 0) return
    e.preventDefault()
    const target = e.currentTarget
    target.setPointerCapture?.(e.pointerId)
    const start = { x: e.clientX, y: e.clientY, pos, width: w }
    const move = (ev: PointerEvent) => onMove(ev.clientX - start.x, ev.clientY - start.y, start)
    const up = () => {
      target.removeEventListener('pointermove', move)
      target.removeEventListener('pointerup', up)
      target.removeEventListener('pointercancel', up)
    }
    target.addEventListener('pointermove', move)
    target.addEventListener('pointerup', up)
    target.addEventListener('pointercancel', up)
  }

  const onMoveDrag = (e: ReactPointerEvent<HTMLElement>) => drag(e, (dx, dy, s) => setPos(keepOnScreen({ right: s.pos.right - dx, bottom: s.pos.bottom - dy })))
  // The corner handle sits top-left, so dragging left widens the box.
  const onResizeDrag = (e: ReactPointerEvent<HTMLElement>) => drag(e, (dx, _dy, s) => onWidth(clampFloatWidth(s.width - dx, window.innerWidth)))

  const onResizeKey = (e: ReactKeyboardEvent<HTMLElement>) => {
    const grow = e.key === 'ArrowLeft' || e.key === 'ArrowUp' || e.key === '+'
    const shrink = e.key === 'ArrowRight' || e.key === 'ArrowDown' || e.key === '-'
    if (!grow && !shrink) return
    e.preventDefault()
    onWidth(clampFloatWidth(w + (grow ? FLOAT_STEP : -FLOAT_STEP), window.innerWidth))
  }

  return createPortal(
    <div ref={box} className="review-float" role="group" aria-label="Floating player" style={{ width: w, right: `min(${pos.right}px, calc(100vw - ${w}px))`, bottom: pos.bottom }}>
      <div className="review-float-bar" onPointerDown={onMoveDrag} title="Drag to move">
        <span className="review-float-grip" aria-hidden="true">⠿</span>
        <span className="review-float-title">Player</span>
        <button type="button" className={buttonClass('ghost', 'sm')} onPointerDown={(e) => e.stopPropagation()} aria-label="Smaller player" onClick={() => onWidth(clampFloatWidth(w - FLOAT_STEP, window.innerWidth))}>
          −
        </button>
        <button type="button" className={buttonClass('ghost', 'sm')} onPointerDown={(e) => e.stopPropagation()} aria-label="Larger player" onClick={() => onWidth(clampFloatWidth(w + FLOAT_STEP, window.innerWidth))}>
          +
        </button>
        <button type="button" className={buttonClass('ghost', 'sm')} onPointerDown={(e) => e.stopPropagation()} onClick={onReturn}>
          Return player
        </button>
      </div>
      <div className="review-float-body" ref={hostRef} />
      <div
        className="review-float-resize"
        role="slider"
        tabIndex={0}
        aria-label="Player size"
        aria-orientation="horizontal"
        aria-valuemin={FLOAT_MIN_WIDTH}
        aria-valuemax={clampFloatWidth(Infinity, viewportWidth)}
        aria-valuenow={w}
        aria-valuetext={`${w} pixels wide`}
        title="Drag to resize, or use the arrow keys"
        onPointerDown={onResizeDrag}
        onKeyDown={onResizeKey}
      />
    </div>,
    document.body,
  )
}
