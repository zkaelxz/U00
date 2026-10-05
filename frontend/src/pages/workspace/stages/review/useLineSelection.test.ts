import { describe, expect, it, vi } from 'vitest'

import { formatLineNumbers, LineSelectionStore } from './useLineSelection'

const lines = (ids: number[]) => ids.map((id) => ({ id, number: id - 100 }))
const make = () => {
  const s = new LineSelectionStore(1)
  s.setVisible(lines([101, 102, 103, 104, 105]))
  return s
}

describe('LineSelectionStore', () => {
  it('toggles a line on and off', () => {
    const s = make()
    s.toggle(102)
    expect(s.sorted()).toEqual([102])
    s.toggle(102)
    expect(s.sorted()).toEqual([])
  })

  it('selects a range in the order shown, in either direction', () => {
    const s = make()
    s.selectRange(102, 104)
    expect(s.sorted()).toEqual([102, 103, 104])
    s.clear()
    s.selectRange(105, 103)
    expect(s.sorted()).toEqual([103, 104, 105])
  })

  it('ranges follow the visible order, not the id order', () => {
    const s = new LineSelectionStore(1)
    s.setVisible(lines([105, 101, 103]))
    s.selectRange(105, 103)
    expect(s.sorted()).toEqual([101, 103, 105])
  })

  it('shift-tick extends from the last ticked line; without an anchor it ticks one', () => {
    const s = make()
    s.tick(104, true)
    expect(s.sorted()).toEqual([104])
    s.tick(102, true)
    expect(s.sorted()).toEqual([102, 103, 104])
  })

  it('selectMany adds to the selection and notifies once, or not at all when nothing is new', () => {
    const s = make()
    const fn = vi.fn()
    s.subscribe(fn)
    s.selectMany([101, 102])
    s.selectMany([102])
    expect(fn).toHaveBeenCalledTimes(1)
    expect(s.sorted()).toEqual([101, 102])
  })

  it('keeps the selection when the visible lines change (filter, search, page)', () => {
    const s = make()
    s.selectMany([101, 103])
    s.setVisible(lines([103, 110]))
    expect(s.sorted()).toEqual([101, 103])
    expect(s.lineNumbers(s.sorted())).toEqual([1, 3])
  })

  it('clear empties it', () => {
    const s = make()
    s.selectMany([101, 102])
    s.clear()
    expect(s.ids.size).toBe(0)
  })

  it('clears when the title changes, but not when the same title is set again', () => {
    const s = make()
    s.selectMany([101, 102])
    s.setScope(1)
    expect(s.ids.size).toBe(2)
    s.setScope(2)
    expect(s.ids.size).toBe(0)
    expect(s.lineNumbers([101])).toEqual([])
  })
})

describe('formatLineNumbers', () => {
  it('collapses runs of consecutive numbers', () => {
    expect(formatLineNumbers([12, 14, 15, 16, 17, 18])).toBe('#12, #14-#18')
  })
  it('sorts and de-duplicates', () => {
    expect(formatLineNumbers([3, 1, 2, 2, 9])).toBe('#1-#3, #9')
    expect(formatLineNumbers([])).toBe('')
  })
})
