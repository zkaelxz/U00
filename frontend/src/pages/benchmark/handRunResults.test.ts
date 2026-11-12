import { describe, expect, it } from 'vitest'

import { costRanks, HAND_RUN_TESTS, speedRanks } from './handRunResults'

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
})
