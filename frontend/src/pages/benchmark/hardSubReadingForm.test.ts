import { describe, expect, it } from 'vitest'

import type { HardSubReadingOutcome } from '../../types/benchmark'
import { clock, describeOverall, describeReading, groupRows } from './hardSubReadingForm'

const outcome = (over: Partial<HardSubReadingOutcome> = {}): HardSubReadingOutcome => ({
  groups: [{ key: 'white-left', lines: [{ start: 0, end: 3, text: '你好' }, { start: 4, end: 6, text: '再见' }] }],
  windows_read: 40, windows_total: 40, unreadable: 0, unclear_pictures: 0, failed_reads: 0, cost_usd: 0.2234,
  stopped: null, note: null,
  comparison: { groups: [{ key: 'white-left', characters: 4, recall: 0.75 }], recall: 0.75, precision: 0.5, label_overlap: {}, label_match: null },
  ...over,
})

describe('hard-sub reading result', () => {
  it('describes a complete reading and an early stop', () => {
    expect(describeReading(outcome())).toBe('Read 40 of 40 stretches. Cost $0.22.')
    expect(describeReading(outcome({ windows_read: 9, stopped: 'cap' }))).toBe('Read 9 of 40 stretches. Cost $0.22. Stopped at the spending limit.')
    expect(describeReading(outcome({ stopped: 'failed', note: 'Provider down.' }))).toContain('Provider down.')
  })

  it('gives one row per group with its recall', () => {
    expect(groupRows(outcome())).toEqual([{ key: 'white-left', lines: 2, characters: 4, recall: '75%' }])
  })

  it('shows n/a, not 0%, when nothing could be compared', () => {
    expect(describeOverall(outcome())).toContain('match the groups for n/a of the shared time')
  })

  it('formats times as m:ss', () => {
    expect(clock(0)).toBe('0:00')
    expect(clock(125.4)).toBe('2:05')
  })
})
