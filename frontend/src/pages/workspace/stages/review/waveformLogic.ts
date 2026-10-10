// Pure helpers for the Review waveform timeline: the visible window, time <->
// pixel mapping, peak columns, and where a line's edges may be dragged to.

import type { ReviewLine } from '../../../../types/review'

type Timed = Pick<ReviewLine, 'id' | 'idx' | 'start' | 'end'>

export interface View {
  start: number
  span: number
}

// A line shorter than this is unreadable on the timeline and nearly unplayable.
export const MIN_LINE_SECONDS = 0.1
export const MIN_SPAN = 2
export const MAX_SPAN = 120
export const DEFAULT_SPAN = 20
export const NUDGE_SECONDS = 0.05

// Round to the millisecond so a drag sends a clean number, not 12.340000001.
export const round3 = (s: number) => Math.round(s * 1000) / 1000

export function clampSpan(span: number): number {
  return Math.min(MAX_SPAN, Math.max(MIN_SPAN, span))
}

// The window shown for `span` seconds centred on `center`, kept inside the media.
export function viewAround(center: number, span: number, duration: number): View {
  const s = clampSpan(span)
  let start = center - s / 2
  if (Number.isFinite(duration) && duration > s) start = Math.min(start, duration - s)
  return { start: round3(Math.max(0, start)), span: s }
}

export function viewForLine(line: Pick<ReviewLine, 'start' | 'end'>, span: number, duration: number): View {
  // A long line must fit with a margin; a short one is shown in the default window.
  return viewAround((line.start + line.end) / 2, Math.max(span, (line.end - line.start) * 1.5), duration)
}

// Zoom about the middle of the window (factor < 1 zooms in).
export function zoomView(view: View, factor: number, duration: number): View {
  return viewAround(view.start + view.span / 2, view.span * factor, duration)
}

export function panView(view: View, seconds: number, duration: number): View {
  return viewAround(view.start + view.span / 2 + seconds, view.span, duration)
}

export const timeToX = (t: number, view: View, width: number) => ((t - view.start) / view.span) * width
export const xToTime = (x: number, view: View, width: number) => view.start + (x / width) * view.span

// Peaks (0-255) -> one 0..1 height per pixel column, the loudest bucket in each.
export function peakColumns(peaks: number[], columns: number): number[] {
  const out = new Array<number>(Math.max(0, columns)).fill(0)
  if (peaks.length === 0) return out
  for (let x = 0; x < columns; x += 1) {
    const lo = Math.floor((x * peaks.length) / columns)
    const hi = Math.max(lo + 1, Math.floor(((x + 1) * peaks.length) / columns))
    let m = 0
    for (let i = lo; i < hi && i < peaks.length; i += 1) m = Math.max(m, peaks[i])
    out[x] = m / 255
  }
  return out
}

// Buckets to request: about one per two pixels, within the server's 16..2000.
export function bucketsFor(width: number): number {
  return Math.min(2000, Math.max(16, Math.round(width / 2)))
}

export function visibleLines<T extends Pick<ReviewLine, 'start' | 'end'>>(lines: T[], view: View): T[] {
  return lines.filter((l) => l.end > view.start && l.start < view.start + view.span)
}

export interface EdgeBounds {
  // The earliest and latest the edge may sit.
  min: number
  max: number
}

// Where each edge of `line` may go. A neighbour that isn't on screen (another
// page, a filter, a search) makes the side facing it locked: the line could
// otherwise be dragged over a line we can't see. That includes the end of the
// last line of the title, since the page doesn't say whether it is the last.
export function edgeBounds(lines: Timed[], line: Timed): { start: EdgeBounds; end: EdgeBounds } {
  const prev = lines.find((l) => l.idx === line.idx - 1)
  const next = lines.find((l) => l.idx === line.idx + 1)
  const floor = line.idx === 0 ? 0 : prev ? prev.end : line.start
  const ceil = next ? next.start : line.end
  return {
    start: { min: Math.min(floor, line.start), max: Math.max(line.start, line.end - MIN_LINE_SECONDS) },
    end: { min: Math.min(line.end, line.start + MIN_LINE_SECONDS), max: Math.max(ceil, line.end) },
  }
}

export function clampEdge(proposed: number, bounds: EdgeBounds): number {
  return round3(Math.min(bounds.max, Math.max(bounds.min, proposed)))
}
