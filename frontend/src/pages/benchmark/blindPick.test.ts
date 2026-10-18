import { describe, expect, it } from 'vitest'

import { pickSummaryLine, pickTotalsLine } from './blindPickText'

describe('blind pick text', () => {
  it('says nothing for a run nobody picked', () => {
    expect(pickTotalsLine(null)).toBe('')
  })

  it('shows wins, losses and ties only when there are ties', () => {
    expect(pickTotalsLine({ won: 3, lost: 1, tie: 0 })).toBe('Your picks: 3 won · 1 lost')
    expect(pickTotalsLine({ won: 1, lost: 1, tie: 2 })).toBe('Your picks: 1 won · 1 lost · 2 tied')
  })

  it('reports how often the judge matched the owner, leaving out ties and unscored cases', () => {
    const s = { run_a: 1, run_b: 2, total: 5, picked: 4, a_wins: 2, b_wins: 1, ties: 1,
      judge_agrees: 2, judge_disagrees: 1, judge_undecided: 1 }
    expect(pickSummaryLine(s)).toBe('Picked 4 of 5: first run 2, second run 1, tied 1 · the judge agreed with you on 2 of 3')
    expect(pickSummaryLine({ ...s, judge_agrees: 0, judge_disagrees: 0 })).not.toContain('judge')
  })
})
