import { describe, expect, it } from 'vitest'

import { squarify } from './treemap'

const area = (r: { w: number; h: number }) => r.w * r.h

describe('squarify', () => {
  const items = [
    { id: 'a', value: 600 }, { id: 'b', value: 300 }, { id: 'c', value: 100 },
    { id: 'd', value: 50 }, { id: 'e', value: 50 }, { id: 'f', value: 0 },
  ]
  const rects = squarify(items, 100, 56)

  it('gives each positive item an area proportional to its value and skips zeros', () => {
    expect(rects.map((r) => r.id).sort()).toEqual(['a', 'b', 'c', 'd', 'e'])
    const total = 100 * 56
    for (const r of rects) {
      const v = items.find((i) => i.id === r.id)!.value
      expect(area(r)).toBeCloseTo((v / 1100) * total, 6)
    }
    expect(rects.reduce((s, r) => s + area(r), 0)).toBeCloseTo(total, 6)
  })

  it('stays inside the box and never overlaps', () => {
    for (const r of rects) {
      expect(r.x).toBeGreaterThanOrEqual(-1e-9)
      expect(r.y).toBeGreaterThanOrEqual(-1e-9)
      expect(r.x + r.w).toBeLessThanOrEqual(100 + 1e-9)
      expect(r.y + r.h).toBeLessThanOrEqual(56 + 1e-9)
    }
    for (let i = 0; i < rects.length; i++) {
      for (let j = i + 1; j < rects.length; j++) {
        const a = rects[i]
        const b = rects[j]
        const ox = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x)
        const oy = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y)
        expect(ox > 1e-9 && oy > 1e-9).toBe(false)
      }
    }
  })

  it('puts the biggest item first and keeps cells reasonably square', () => {
    expect(rects[0].id).toBe('a')
    for (const r of rects) expect(Math.max(r.w / r.h, r.h / r.w)).toBeLessThan(6)
  })

  it('handles one item, none, and a degenerate box', () => {
    expect(squarify([{ id: 'x', value: 5 }], 100, 50)).toEqual([{ id: 'x', x: 0, y: 0, w: 100, h: 50 }])
    expect(squarify([], 100, 50)).toEqual([])
    expect(squarify([{ id: 'x', value: 0 }], 100, 50)).toEqual([])
    expect(squarify([{ id: 'x', value: 5 }], 0, 50)).toEqual([])
  })
})
