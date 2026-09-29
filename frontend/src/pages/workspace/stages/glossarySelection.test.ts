import { describe, expect, it } from 'vitest'

import { pruneSelection, selectedInOrder, toggleAll, toggleId } from './glossarySelection'

describe('glossary selection', () => {
  it('toggles one id without mutating the input', () => {
    const start = new Set([1])
    expect([...toggleId(start, 2)]).toEqual([1, 2])
    expect([...toggleId(start, 1)]).toEqual([])
    expect([...start]).toEqual([1])
  })

  it('selects all, then clears when all are selected', () => {
    expect([...toggleAll(new Set([2]), [1, 2, 3])]).toEqual([1, 2, 3])
    expect([...toggleAll(new Set([1, 2, 3]), [1, 2, 3])]).toEqual([])
    expect([...toggleAll(new Set(), [])]).toEqual([])
  })

  it('prunes ids that disappeared and orders by the list', () => {
    expect([...pruneSelection(new Set([1, 9]), [1, 2])]).toEqual([1])
    expect(selectedInOrder(new Set([3, 1]), [1, 2, 3])).toEqual([1, 3])
  })
})
