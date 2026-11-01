// Frame arithmetic for the frame timing view: times are seconds, frames count from 0 at the start of the video.
import { clampEdge, type EdgeBounds } from './waveformLogic'

export const FALLBACK_FPS = 24
export const FRAME_JUMP = 10

export const frameOf = (seconds: number, fps: number) => Math.round(seconds * fps)

// h:mm:ss.mmm under an hour is mm:ss.mmm, like the mock-up.
export function frameClock(seconds: number): string {
  const total = Math.max(0, Math.round(seconds * 1000))
  const m = Math.floor(total / 60000)
  const s = Math.floor((total % 60000) / 1000)
  return `${m}:${String(s).padStart(2, '0')}.${String(total % 1000).padStart(3, '0')}`
}

// `frames` earlier (negative) or later, kept inside the edge's bounds.
export function stepFrames(seconds: number, frames: number, fps: number, bounds: EdgeBounds): number {
  return clampEdge((frameOf(seconds, fps) + frames) / fps, bounds)
}
