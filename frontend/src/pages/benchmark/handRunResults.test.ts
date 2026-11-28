import { describe, expect, it } from 'vitest'

import { bestValueRow, costRanks, HAND_RUN_TESTS, qualityRanks, speedRanks, type HandRunTest } from './handRunResults'

const row = (model: string, over: object = {}) => ({ model, reading: '', ...over })

describe('hand-run results', () => {
  it('ranks the fastest row 1, lets equal times share a rank, and leaves unmeasured rows unranked', () => {
    const rows = [row('a', { seconds: 5 }), row('b', { seconds: 2 }), row('c', { seconds: 5 }), row('d')]
    expect(speedRanks(rows)).toEqual([2, 1, 2, null])
  })

  it('ranks the cheapest row 1 and skips rows with no dollar cost', () => {
    const rows = [row('a', { costUsd: 0.5 }), row('b', { costUsd: 0.1 }), row('c', { costLabel: 'plan credit' })]
    expect(costRanks(rows)).toEqual([2, 1, null])
  })

  it('lists each model once per test and every test names its Library source', () => {
    for (const t of HAND_RUN_TESTS) {
      const names = t.rows.map((r) => r.model)
      expect(new Set(names).size).toBe(names.length)
      expect(t.source).not.toBe('')
    }
  })

  it('ranks by score where the rows have one, and by the written reading otherwise', () => {
    const base = { id: 't', title: 't', date: '2026-11-28', basis: 'b', source: 's', note: 'n' }
    const scored: HandRunTest = { ...base, rows: [row('a', { score: 60 }), row('b', { score: 63 }), row('c', { score: 60 }), row('d')] }
    expect(qualityRanks(scored)).toMatchObject({ ranks: [2, 1, 2, null], judged: false })
    const read: HandRunTest = { ...base, rows: [row('a', { qualityTier: 2 }), row('b', { qualityTier: 1 }), row('c')] }
    expect(qualityRanks(read)).toMatchObject({ ranks: [2, 1, null], judged: true })
  })

  it('picks the best value among the top-quality rows only', () => {
    const base = { id: 't', title: 't', date: 'd', basis: 'b', source: 's', note: 'n' }
    const scored: HandRunTest = {
      ...base,
      rows: [row('best', { score: 63, costUsd: 0.14 }), row('close and cheap', { score: 61.5, costUsd: 0.1 }), row('far and cheaper', { score: 50, costUsd: 0.01 })],
    }
    expect(bestValueRow(scored)).toBe(1)
    expect(bestValueRow({ ...base, rows: [row('free', { score: 70, costUsd: 0 })] })).toBe(-1)
  })

  it('every test is dated, and each row\'s quality is scored or read', () => {
    for (const t of HAND_RUN_TESTS) {
      expect(t.date).toMatch(/^\d{4}-\d{2}-\d{2}$/)
      expect(qualityRanks(t).ranks.some((r) => r != null)).toBe(true)
    }
  })
})
