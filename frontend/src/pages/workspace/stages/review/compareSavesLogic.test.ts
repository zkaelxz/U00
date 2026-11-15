import { describe, expect, it } from 'vitest'
import { compareSaves, isChanged, stepChanged, wordDiff } from './compareSavesLogic'

const L = (id: number, idx: number, en: string) => ({ id, idx, zh: `z${id}`, en })

describe('compareSaves', () => {
  it('matches by id, not position, and keeps one-sided lines', () => {
    const rows = compareSaves([L(1, 0, 'a'), L(2, 1, 'b')], [L(2, 0, 'b'), L(3, 1, 'c')])
    expect(rows.map((r) => [r.id, r.left, r.right])).toEqual([[1, 'a', null], [2, 'b', 'b'], [3, null, 'c']])
    expect(rows.filter(isChanged).map((r) => r.id)).toEqual([1, 3])
  })
})

describe('wordDiff', () => {
  it('marks only the words that differ', () => {
    expect(wordDiff('I will go home', 'I shall go home').filter((p) => p.changed).map((p) => p.text)).toEqual(['will'])
  })
  it('marks nothing for equal text', () => {
    expect(wordDiff('same', 'same').some((p) => p.changed)).toBe(false)
  })
})

describe('stepChanged', () => {
  const rows = compareSaves([L(1, 0, 'a'), L(2, 1, 'b'), L(3, 2, 'c')], [L(1, 0, 'x'), L(2, 1, 'b'), L(3, 2, 'y')])
  it('wraps in both directions', () => {
    expect(stepChanged(rows, 0, 1)).toBe(2)
    expect(stepChanged(rows, 2, 1)).toBe(0)
    expect(stepChanged(rows, 0, -1)).toBe(2)
  })
  it('returns -1 when nothing differs', () => {
    expect(stepChanged(compareSaves([L(1, 0, 'a')], [L(1, 0, 'a')]), 0, 1)).toBe(-1)
  })
})
