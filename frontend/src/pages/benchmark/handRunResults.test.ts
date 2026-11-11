import { describe, expect, it } from 'vitest'

import { HAND_RUN_ROWS, speedRanks } from './handRunResults'

describe('hand-run results', () => {
  it('ranks the fastest row 1 and lets equal times share a rank', () => {
    const rows = [
      { model: 'a', seconds: 5, costUsd: 0, reading: '' },
      { model: 'b', seconds: 2, costUsd: 0, reading: '' },
      { model: 'c', seconds: 5, costUsd: 0, reading: '' },
    ]
    expect(speedRanks(rows)).toEqual([2, 1, 2])
  })

  it('lists each model and setting once', () => {
    const names = HAND_RUN_ROWS.map((r) => r.model)
    expect(new Set(names).size).toBe(names.length)
  })
})
