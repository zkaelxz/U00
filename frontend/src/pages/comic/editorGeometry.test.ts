import { describe, expect, it } from 'vitest'

import { clampBox, drawnBox, MIN_BOX, moveBox, pageToScreen, resizeBox, sameBox, screenToPage } from './editorGeometry'

const page = { width: 800, height: 1200 }

describe('coordinate mapping', () => {
  it('maps a screen distance to page pixels and back', () => {
    expect(screenToPage(50, 0.5)).toBe(100)
    expect(pageToScreen(100, 0.5)).toBe(50)
    expect(pageToScreen(screenToPage(37, 0.25), 0.25)).toBeCloseTo(37)
  })
  it('does not divide by a zero scale', () => {
    expect(screenToPage(10, 0)).toBe(0)
  })
})

describe('moveBox', () => {
  const box = { x: 100, y: 200, w: 300, h: 100 }
  it('moves by the delta', () => {
    expect(moveBox(box, 20, -30, page)).toEqual({ x: 120, y: 170, w: 300, h: 100 })
  })
  it('stops at every page edge without changing size', () => {
    expect(moveBox(box, -500, -500, page)).toEqual({ x: 0, y: 0, w: 300, h: 100 })
    expect(moveBox(box, 5000, 5000, page)).toEqual({ x: 500, y: 1100, w: 300, h: 100 })
  })
  it('rounds to whole pixels', () => {
    expect(moveBox(box, 0.4, 0.6, page)).toEqual({ x: 100, y: 201, w: 300, h: 100 })
  })
})

describe('resizeBox', () => {
  const box = { x: 100, y: 200, w: 300, h: 100 }
  it('drags the south-east corner and keeps the north-west one', () => {
    expect(resizeBox(box, 'se', 40, 20, page)).toEqual({ x: 100, y: 200, w: 340, h: 120 })
  })
  it('drags the north-west corner and keeps the south-east one', () => {
    expect(resizeBox(box, 'nw', -50, -20, page)).toEqual({ x: 50, y: 180, w: 350, h: 120 })
  })
  it('drags the other two corners', () => {
    expect(resizeBox(box, 'ne', 10, -10, page)).toEqual({ x: 100, y: 190, w: 310, h: 110 })
    expect(resizeBox(box, 'sw', -10, 10, page)).toEqual({ x: 90, y: 200, w: 310, h: 110 })
  })
  it('stops at the page edges', () => {
    expect(resizeBox(box, 'se', 9999, 9999, page)).toEqual({ x: 100, y: 200, w: 700, h: 1000 })
    expect(resizeBox(box, 'nw', -9999, -9999, page)).toEqual({ x: 0, y: 0, w: 400, h: 300 })
  })
  it('never shrinks below the minimum or flips over', () => {
    expect(resizeBox(box, 'se', -9999, -9999, page)).toEqual({ x: 100, y: 200, w: MIN_BOX, h: MIN_BOX })
    expect(resizeBox(box, 'nw', 9999, 9999, page)).toEqual({ x: 392, y: 292, w: MIN_BOX, h: MIN_BOX })
  })
})

describe('clampBox', () => {
  it('pulls an oversized or outside box into the page', () => {
    expect(clampBox({ x: -5, y: 1190, w: 2000, h: 50 }, page)).toEqual({ x: 0, y: 1150, w: 800, h: 50 })
  })
})

describe('sameBox', () => {
  it('compares all four numbers', () => {
    expect(sameBox({ x: 1, y: 2, w: 3, h: 4 }, { x: 1, y: 2, w: 3, h: 4 })).toBe(true)
    expect(sameBox({ x: 1, y: 2, w: 3, h: 4 }, { x: 1, y: 2, w: 3, h: 5 })).toBe(false)
  })
})

describe('drawnBox', () => {
  it('is the rectangle between the two points', () => {
    expect(drawnBox({ x: 100, y: 200 }, { x: 300, y: 260 }, page)).toEqual({ x: 100, y: 200, w: 200, h: 60 })
  })
  it('reads a drag from the bottom right to the top left the same way', () => {
    expect(drawnBox({ x: 300, y: 260 }, { x: 100, y: 200 }, page)).toEqual({ x: 100, y: 200, w: 200, h: 60 })
    expect(drawnBox({ x: 300, y: 200 }, { x: 100, y: 260 }, page)).toEqual({ x: 100, y: 200, w: 200, h: 60 })
  })
  it('keeps a drag that leaves the page on the page edge', () => {
    expect(drawnBox({ x: 700, y: 1100 }, { x: 5000, y: 9000 }, page)).toEqual({ x: 700, y: 1100, w: 100, h: 100 })
    expect(drawnBox({ x: 50, y: 40 }, { x: -500, y: -500 }, page)).toEqual({ x: 0, y: 0, w: 50, h: 40 })
  })
  it('rounds to whole pixels', () => {
    expect(drawnBox({ x: 10.4, y: 10.4 }, { x: 60.6, y: 40.2 }, page)).toEqual({ x: 10, y: 10, w: 50, h: 30 })
  })
  it('ignores a click and a drag under the minimum in either direction', () => {
    expect(drawnBox({ x: 100, y: 100 }, { x: 100, y: 100 }, page)).toBeNull()
    expect(drawnBox({ x: 100, y: 100 }, { x: 100 + MIN_BOX - 1, y: 400 }, page)).toBeNull()
    expect(drawnBox({ x: 100, y: 100 }, { x: 400, y: 100 + MIN_BOX - 1 }, page)).toBeNull()
    expect(drawnBox({ x: 100, y: 100 }, { x: 100 + MIN_BOX, y: 100 + MIN_BOX }, page)).toEqual({ x: 100, y: 100, w: MIN_BOX, h: MIN_BOX })
  })
  it('ignores a drag that only grazes the page edge', () => {
    expect(drawnBox({ x: 796, y: 100 }, { x: 900, y: 300 }, page)).toBeNull()
  })
})
