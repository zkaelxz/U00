// Pinch zoom, pan and taps for the comic stage, from pointer events. The
// handlers go on an untransformed box; the returned zoom is applied as a CSS
// transform to its child. While not zoomed the stage keeps native scrolling
// (touch-action pan-x pan-y); once zoomed a one-finger drag pans instead.
import { useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type RefObject } from 'react'

import { clampZoom, NO_ZOOM, toggleZoomAt, zoomAt, type Zoom } from './comicLogic'

const TAP_MOVE_PX = 10
const TAP_MAX_MS = 350
const DOUBLE_TAP_MS = 280
const DOUBLE_TAP_PX = 40

type Pt = { x: number; y: number }

interface Gesture {
  startZoom: Zoom
  // First pointer down (box coordinates) and when.
  downAt: number
  down: Pt
  // Pinch start: finger distance and midpoint.
  startDist: number
  startMid: Pt
  pinched: boolean
  moved: boolean
}

function measure(el: HTMLElement | null) {
  if (!el) return null
  const r = el.getBoundingClientRect()
  return { r, w: el.clientWidth || r.width, h: el.clientHeight || r.height }
}

const local = (e: { clientX: number; clientY: number }, r: DOMRect): Pt => ({ x: e.clientX - r.left, y: e.clientY - r.top })

export function useZoom(
  boxRef: RefObject<HTMLElement | null>,
  onTap: (fraction: number) => void,
  resetKey: unknown,
) {
  // The zoom belongs to one resetKey (page, mode, fit); a new key starts unzoomed.
  const [state, setState] = useState<{ key: unknown; zoom: Zoom }>({ key: resetKey, zoom: NO_ZOOM })
  const zoom = state.key === resetKey ? state.zoom : NO_ZOOM
  const zoomRef = useRef<Zoom>(zoom)
  const keyRef = useRef(resetKey)
  const pointers = useRef(new Map<number, Pt>())
  const gesture = useRef<Gesture | null>(null)
  const lastTap = useRef<{ t: number; p: Pt } | null>(null)
  const tapTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const tapRef = useRef(onTap)
  useEffect(() => {
    tapRef.current = onTap
    keyRef.current = resetKey
    zoomRef.current = zoom
  })

  const setZoom = useCallback((z: Zoom) => {
    zoomRef.current = z
    setState({ key: keyRef.current, zoom: z })
  }, [])

  useEffect(() => () => clearTimeout(tapTimer.current), [])

  const box = () => measure(boxRef.current)

  const zoomBy = useCallback(
    (factor: number) => {
      const b = measure(boxRef.current)
      if (!b) return
      // Around the middle of the part of the box that is on screen.
      const cy = Math.min(b.h, Math.max(0, window.innerHeight / 2 - b.r.top))
      setZoom(zoomAt(zoomRef.current, factor, b.w / 2, cy, b.w, b.h))
    },
    [boxRef, setZoom],
  )

  const onPointerDown = (e: ReactPointerEvent<HTMLElement>) => {
    if (e.pointerType === 'mouse' && e.button !== 0) return
    const b = box()
    if (!b) return
    const p = local(e, b.r)
    pointers.current.set(e.pointerId, p)
    if (e.pointerType === 'mouse') {
      try {
        e.currentTarget.setPointerCapture(e.pointerId)
      } catch {
        // Capture is a nicety for dragging past the edge.
      }
    }
    if (pointers.current.size === 1) {
      gesture.current = { startZoom: zoomRef.current, downAt: e.timeStamp, down: p, startDist: 0, startMid: p, pinched: false, moved: false }
    } else if (pointers.current.size === 2 && gesture.current) {
      const [a, c] = [...pointers.current.values()]
      gesture.current.startDist = Math.hypot(a.x - c.x, a.y - c.y) || 1
      gesture.current.startMid = { x: (a.x + c.x) / 2, y: (a.y + c.y) / 2 }
      gesture.current.startZoom = zoomRef.current
      gesture.current.pinched = true
    }
  }

  const onPointerMove = (e: ReactPointerEvent<HTMLElement>) => {
    const g = gesture.current
    if (!g || !pointers.current.has(e.pointerId)) return
    const b = box()
    if (!b) return
    const p = local(e, b.r)
    pointers.current.set(e.pointerId, p)
    if (Math.hypot(p.x - g.down.x, p.y - g.down.y) > TAP_MOVE_PX) g.moved = true
    if (pointers.current.size >= 2) {
      const [a, c] = [...pointers.current.values()]
      const dist = Math.hypot(a.x - c.x, a.y - c.y) || 1
      const mid = { x: (a.x + c.x) / 2, y: (a.y + c.y) / 2 }
      const z = zoomAt(g.startZoom, dist / g.startDist, g.startMid.x, g.startMid.y, b.w, b.h)
      setZoom(clampZoom({ ...z, x: z.x + mid.x - g.startMid.x, y: z.y + mid.y - g.startMid.y }, b.w, b.h))
    } else if (g.startZoom.scale > 1 && !g.pinched) {
      setZoom(clampZoom({ ...g.startZoom, x: g.startZoom.x + p.x - g.down.x, y: g.startZoom.y + p.y - g.down.y }, b.w, b.h))
    }
  }

  const end = (e: ReactPointerEvent<HTMLElement>, cancelled: boolean) => {
    const had = pointers.current.delete(e.pointerId)
    const g = gesture.current
    if (!had || !g) return
    if (pointers.current.size > 0) {
      // One finger left after a pinch: carry on panning from here.
      const [rest] = [...pointers.current.values()]
      g.down = rest
      g.startZoom = zoomRef.current
      return
    }
    gesture.current = null
    if (cancelled || g.pinched || g.moved || e.timeStamp - g.downAt > TAP_MAX_MS) return
    const b = box()
    if (!b) return
    const p = g.down
    const fraction = b.w > 0 ? p.x / b.w : 0.5
    if (e.pointerType === 'mouse') {
      tapRef.current(fraction)
      return
    }
    // Touch: wait briefly so a second tap can make it a double-tap (zoom).
    const prev = lastTap.current
    if (prev && e.timeStamp - prev.t < DOUBLE_TAP_MS && Math.hypot(p.x - prev.p.x, p.y - prev.p.y) < DOUBLE_TAP_PX) {
      clearTimeout(tapTimer.current)
      lastTap.current = null
      setZoom(toggleZoomAt(zoomRef.current, p.x, p.y, b.w, b.h))
      return
    }
    lastTap.current = { t: e.timeStamp, p }
    clearTimeout(tapTimer.current)
    tapTimer.current = setTimeout(() => {
      lastTap.current = null
      tapRef.current(fraction)
    }, DOUBLE_TAP_MS)
  }

  return {
    zoom,
    zoomBy,
    reset: () => setZoom(NO_ZOOM),
    handlers: {
      onPointerDown,
      onPointerMove,
      onPointerUp: (e: ReactPointerEvent<HTMLElement>) => end(e, false),
      onPointerCancel: (e: ReactPointerEvent<HTMLElement>) => end(e, true),
    },
  }
}
