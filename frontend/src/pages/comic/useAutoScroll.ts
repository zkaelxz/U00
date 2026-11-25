// Auto-scroll for the vertical comic view: the page glides down by itself so a long strip can be read hands-free.
import { useEffect, useState } from 'react'

export type ScrollSpeed = 'off' | 'slow' | 'medium' | 'fast'
export const SCROLL_SPEEDS: [ScrollSpeed, string][] = [
  ['off', 'Off'],
  ['slow', 'Slow'],
  ['medium', 'Medium'],
  ['fast', 'Fast'],
]
// Pixels per second. Slow is about a line of speech bubbles every few seconds on a phone.
export const SCROLL_PX_PER_SECOND: Record<ScrollSpeed, number> = { off: 0, slow: 45, medium: 100, fast: 180 }

export function useAutoScroll(vertical: boolean) {
  const [speed, setSpeed] = useState<ScrollSpeed>('off')
  const running = vertical && speed !== 'off'

  useEffect(() => {
    if (!running) return
    const pxPerMs = SCROLL_PX_PER_SECOND[speed] / 1000
    let frame = 0
    let last = performance.now()
    // scrollBy drops fractions, so the sub-pixel remainder is carried to the next frame.
    let carry = 0
    const tick = (now: number) => {
      carry += (now - last) * pxPerMs
      last = now
      const whole = Math.floor(carry)
      if (whole > 0) {
        carry -= whole
        window.scrollBy(0, whole)
      }
      frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    // Whoever scrolls by hand takes over: the glide stops instead of fighting the finger.
    const stop = () => setSpeed('off')
    const onKey = (e: KeyboardEvent) => {
      if (['ArrowDown', 'ArrowUp', 'PageDown', 'PageUp', 'Home', 'End', ' '].includes(e.key)) stop()
    }
    window.addEventListener('wheel', stop, { passive: true })
    window.addEventListener('touchmove', stop, { passive: true })
    window.addEventListener('keydown', onKey)
    return () => {
      cancelAnimationFrame(frame)
      window.removeEventListener('wheel', stop)
      window.removeEventListener('touchmove', stop)
      window.removeEventListener('keydown', onKey)
    }
  }, [running, speed])

  return { speed, setSpeed }
}
