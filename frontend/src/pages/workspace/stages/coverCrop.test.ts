import { describe, expect, it } from 'vitest'

import { COVER_MAX_WIDTH, cropRect, largestBox, outputSize } from './coverCrop'

describe('cover crop', () => {
  it('fits the largest 2:3 box in a wide frame by its height', () => {
    const box = largestBox(1920, 1080)
    expect(box.h).toBe(1080)
    expect(box.w).toBeCloseTo(720)
  })

  it('fits a tall page by its width', () => {
    const box = largestBox(800, 3000)
    expect(box.w).toBe(800)
    expect(box.h).toBeCloseTo(1200)
  })

  it('keeps the box inside the picture at every position', () => {
    for (const [px, py] of [[0, 0], [1, 1], [0.5, 0.5], [2, -1]]) {
      const r = cropRect(1920, 1080, 1, px, py)
      expect(r.x).toBeGreaterThanOrEqual(0)
      expect(r.y).toBeGreaterThanOrEqual(0)
      expect(r.x + r.w).toBeLessThanOrEqual(1920)
      expect(r.y + r.h).toBeLessThanOrEqual(1080)
    }
  })

  it('shrinks with the size slider and never below a fifth', () => {
    expect(cropRect(1000, 1500, 0.5, 0, 0).w).toBe(500)
    expect(cropRect(1000, 1500, 0, 0, 0).w).toBe(200)
  })

  it('writes at most the maximum width, keeping the ratio', () => {
    const out = outputSize({ x: 0, y: 0, w: 2400, h: 3600 })
    expect(out).toEqual({ w: COVER_MAX_WIDTH, h: 1800 })
  })
})
