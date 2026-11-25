// Swipes on the Watch picture. Only a finger (not a mouse) on the picture itself counts, so the player's own bar and buttons keep their drags.
import { useEffect, type RefObject } from 'react'

import { swipeAction, type SwipeAction } from './swipeLogic'

export function useWatchSwipe(boxRef: RefObject<HTMLElement | null>, enabled: boolean, onSwipe: (action: SwipeAction) => void) {
  useEffect(() => {
    const box = boxRef.current
    if (!box || !enabled) return
    let start: { x: number; y: number; id: number } | null = null
    const down = (e: PointerEvent) => {
      start = e.pointerType === 'touch' && (e.target as Element).closest('.review-stage') ? { x: e.clientX, y: e.clientY, id: e.pointerId } : null
    }
    const up = (e: PointerEvent) => {
      if (!start || start.id !== e.pointerId) return
      const stage = box.querySelector('.review-stage')
      const action = swipeAction(e.clientX - start.x, e.clientY - start.y, stage?.getBoundingClientRect().width ?? 0)
      start = null
      if (action) onSwipe(action)
    }
    const cancel = () => {
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
  }, [boxRef, enabled, onSwipe])
}
