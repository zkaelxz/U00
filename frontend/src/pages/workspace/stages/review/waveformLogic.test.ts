import { describe, expect, it } from 'vitest'

import {
  bucketsFor, clampEdge, clampSpan, edgeBounds, MAX_SPAN, MIN_LINE_SECONDS, MIN_SPAN, panView, peakColumns,
  mergeCuts, snapToCut, timeToX, viewAround, viewForLine, visibleLines, xToTime, zoomView,
} from './waveformLogic'

const L = (id: number, idx: number, start: number, end: number) => ({ id, idx, start, end })

describe('window', () => {
  it('centres on the time and stays inside the media', () => {
    expect(viewAround(50, 20, 600)).toEqual({ start: 40, span: 20 })
    expect(viewAround(3, 20, 600)).toEqual({ start: 0, span: 20 })
    expect(viewAround(598, 20, 600)).toEqual({ start: 580, span: 20 })
    expect(viewAround(5, 20, NaN)).toEqual({ start: 0, span: 20 })
  })
  it('limits the span', () => {
    expect(clampSpan(0.1)).toBe(MIN_SPAN)
    expect(clampSpan(5000)).toBe(MAX_SPAN)
  })
  it('fits a long line with a margin', () => {
    const v = viewForLine({ start: 100, end: 160 }, 20, 1000)
    expect(v.span).toBe(90)
    expect(v.start).toBe(85)
  })
  it('zooms about the middle and pans', () => {
    expect(zoomView({ start: 40, span: 20 }, 0.5, 600)).toEqual({ start: 45, span: 10 })
    expect(panView({ start: 40, span: 20 }, 10, 600)).toEqual({ start: 50, span: 20 })
    expect(panView({ start: 0, span: 20 }, -10, 600).start).toBe(0)
  })
})

describe('time and pixels', () => {
  const view = { start: 10, span: 20 }
  it('maps both ways', () => {
    expect(timeToX(20, view, 1000)).toBe(500)
    expect(xToTime(500, view, 1000)).toBe(20)
    expect(xToTime(timeToX(13.7, view, 800), view, 800)).toBeCloseTo(13.7)
  })
})

describe('peaks', () => {
  it('keeps the loudest bucket per column', () => {
    expect(peakColumns([0, 255, 51, 51], 2)).toEqual([1, 0.2])
  })
  it('spreads buckets over more columns and handles empty input', () => {
    expect(peakColumns([255, 0], 4)).toEqual([1, 1, 0, 0])
    expect(peakColumns([], 3)).toEqual([0, 0, 0])
  })
  it('asks for about a bucket per two pixels within the server limits', () => {
    expect(bucketsFor(10)).toBe(16)
    expect(bucketsFor(800)).toBe(400)
    expect(bucketsFor(99999)).toBe(2000)
  })
  it('lists the lines that touch the window', () => {
    const lines = [L(1, 0, 0, 5), L(2, 1, 5, 12), L(3, 2, 30, 31)]
    expect(visibleLines(lines, { start: 6, span: 10 }).map((l) => l.id)).toEqual([2])
  })
})

describe('edge bounds', () => {
  const lines = [L(1, 3, 10, 12), L(2, 4, 13, 15), L(3, 5, 16, 18)]
  const mid = lines[1]

  it('stops an edge at its neighbours, never over them', () => {
    const b = edgeBounds(lines, mid)
    expect(clampEdge(11, b.start)).toBe(12)
    expect(clampEdge(12.5, b.start)).toBe(12.5)
    expect(clampEdge(17, b.end)).toBe(16)
  })
  it('keeps start before end by a minimum', () => {
    const b = edgeBounds(lines, mid)
    expect(clampEdge(15, b.start)).toBe(15 - MIN_LINE_SECONDS)
    expect(clampEdge(13, b.end)).toBe(13 + MIN_LINE_SECONDS)
  })
  it("does not pass outward over a neighbour that isn't on screen", () => {
    const b = edgeBounds(lines, lines[0])
    expect(clampEdge(5, b.start)).toBe(10)
    expect(clampEdge(11, b.start)).toBe(11)
    const last = edgeBounds(lines, lines[2])
    expect(clampEdge(30, last.end)).toBe(18)
    expect(clampEdge(17, last.end)).toBe(17)
  })
  it('lets the last line of the title run to the end of the media', () => {
    const last = edgeBounds(lines, lines[2], 6, 600)
    expect(clampEdge(30, last.end)).toBe(30)
    expect(clampEdge(900, last.end)).toBe(600)
    expect(clampEdge(30, edgeBounds(lines, lines[2], 9, 600).end)).toBe(18)
  })
  it('lets the first line of the title start at zero', () => {
    const first = edgeBounds([L(1, 0, 2, 4), L(2, 1, 5, 6)], L(1, 0, 2, 4))
    expect(clampEdge(-3, first.start)).toBe(0)
  })
  it('never moves a line already overlapping its neighbour further in', () => {
    const b = edgeBounds([L(1, 0, 0, 6), L(2, 1, 5, 9)], L(2, 1, 5, 9))
    expect(clampEdge(1, b.start)).toBe(5)
    expect(clampEdge(5.5, b.start)).toBe(5.5)
  })
  it('holds both edges of a line shorter than the minimum', () => {
    const b = edgeBounds([L(1, 0, 1, 1.05)], L(1, 0, 1, 1.05))
    expect(clampEdge(1.02, b.start)).toBe(1)
    expect(clampEdge(1.5, b.end)).toBe(1.05)
    expect(clampEdge(1.0, b.end)).toBe(1.05)
  })
})

describe('shot changes', () => {
  const view = { start: 0, span: 20 }

  it('snaps to a cut within 8 pixels, and only then', () => {
    // 1000 px over 20 s: 8 px is 0.16 s.
    expect(snapToCut(4.43, [4.4], view, 1000)).toBe(4.4)
    expect(snapToCut(4.6, [4.4], view, 1000)).toBe(4.6)
  })

  it('takes the nearest of two cuts', () => {
    expect(snapToCut(4.5, [4.4, 4.52], view, 1000)).toBe(4.52)
  })

  it('leaves the time alone with no cuts or no width', () => {
    expect(snapToCut(4.43, [], view, 1000)).toBe(4.43)
    expect(snapToCut(4.43, [4.4], view, 0)).toBe(4.43)
  })

  it('merges found cuts in order without repeats', () => {
    expect(mergeCuts([2, 5], [5, 3, 9])).toEqual([2, 3, 5, 9])
  })
})
