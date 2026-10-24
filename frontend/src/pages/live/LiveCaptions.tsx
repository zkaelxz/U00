/*
 * The caption drawn over the stream picture. It sits in the same box as the
 * iframe (never inside it), so the embed's own security settings are untouched.
 * Clicks pass through to the player.
 */
import { useEffect, useRef, useState } from 'react'

import type { LiveCue } from '../../types/live'
import { captionAt, type Caption } from './captions'

const TICK_MS = 500

export function LiveCaptions({ cues, delay }: { cues: LiveCue[]; delay: number }) {
  // When this browser first saw each cue: the clock the caption timing starts from.
  const arrivals = useRef(new Map<number, number>())
  const [caption, setCaption] = useState<Caption | null>(null)
  const latest = useRef({ cues, delay })

  const now = Date.now()
  for (const c of cues) if (!arrivals.current.has(c.id)) arrivals.current.set(c.id, now)
  // Only the cues the page still keeps: a long session would grow the map without bound.
  if (arrivals.current.size > cues.length) {
    const shown = new Set(cues.map((c) => c.id))
    for (const id of arrivals.current.keys()) if (!shown.has(id)) arrivals.current.delete(id)
  }
  latest.current = { cues, delay }

  useEffect(() => {
    const tick = () => {
      const { cues: cs, delay: d } = latest.current
      const next = captionAt(cs, arrivals.current, d, Date.now())
      setCaption((prev) => (prev && next && prev.id === next.id && prev.text === next.text && prev.pending === next.pending ? prev : next))
    }
    tick()
    const t = setInterval(tick, TICK_MS)
    return () => clearInterval(t)
  }, [cues, delay])

  if (!caption) return null
  return (
    <div className="live-caption" data-testid="live-caption" data-pending={caption.pending || undefined} aria-hidden="true">
      <span>{caption.text}</span>
    </div>
  )
}
