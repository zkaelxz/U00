// Swipes on the Watch picture. Only a single finger (not a mouse) on the picture itself counts, so the player's own bar
// and buttons keep their drags and a pinch is never read as a swipe.
import { useEffect, type RefObject } from 'react'

import { startsAtEdge, swipeSeek } from './swipeLogic'

export function useWatchSwipe(boxRef: RefObject<HTMLElement | null>, enabled: boolean, onSeek: (by: number) => void) {
  useEffect(() => {
    const box = boxRef.current
    if (!box || !enabled) return
    let start: { x: number; y: number; id: number } | null = null
    const touches = new Set<number>()
    const down = (e: PointerEvent) => {
      if (e.pointerType !== 'touch') return
      touches.add(e.pointerId)
      const ok = touches.size === 1 && (e.target as Element).closest('.review-stage') && !startsAtEdge(e.clientX, window.innerWidth)
      start = ok ? { x: e.clientX, y: e.clientY, id: e.pointerId } : null
    }
    const up = (e: PointerEvent) => {
      touches.delete(e.pointerId)
      if (!start || start.id !== e.pointerId) return
      const by = swipeSeek(e.clientX - start.x, e.clientY - start.y)
      start = null
      if (by !== null) onSeek(by)
    }
    const cancel = (e: PointerEvent) => {
      touches.delete(e.pointerId)
      start = null
    }
    box.addEventListener('pointerdown', down)
    box.addEventListener('pointerup', up)
    box.addEventListener('pointercancel', cancel)
    return () => {
      box.removeEventListener('pointerdown', down)
      box.removeEventListener('pointerup', up)
      box.removeEventListener('pointercancel', cancel)
    }
  }, [boxRef, enabled, onSeek])
}
