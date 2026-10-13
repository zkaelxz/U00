import { describe, expect, it } from 'vitest'

import type { JobStageRun } from '../../types/jobs'
import { stageClock, formatStageCost, formatStageDuration, recordedStageRows, runsNote, stageRows, stageTotalLine } from './stageBreakdown'

const run = (o: Partial<JobStageRun> = {}): JobStageRun => ({
  run_started_at: 1000, running: false, total_seconds: 100, cost_usd: 0, stages: [], ...o,
})
const stage = (name: string, duration_seconds: number, cost_usd = 0) =>
  ({ stage: name, started_at: 1000, duration_seconds, cost_usd })

describe('formatStageDuration', () => {
  it.each([
    [0, '0 s'], [0.04, '0 s'], [3.44, '3.4 s'], [9.94, '9.9 s'], [9.96, '10 s'], [42.4, '42 s'],
    [59.6, '1 min'], [72, '1 min 12 s'], [120, '2 min'], [3725, '1 h 02 min'],
    [-5, '0 s'], [Number.NaN, '0 s'],
  ])('%s -> %s', (s, text) => expect(formatStageDuration(s)).toBe(text))
})

describe('formatStageCost', () => {
  it('shows spend to the cent, flags tiny amounts, hides none', () => {
    expect(formatStageCost(0.031)).toBe('$0.03')
    expect(formatStageCost(1.5)).toBe('$1.50')
    expect(formatStageCost(0.001)).toBe('under $0.01')
    expect(formatStageCost(0)).toBeNull()
    expect(formatStageCost(-1)).toBeNull()
  })
})

describe('stageRows', () => {
  it('gives each stage its share of the run total, in run order', () => {
    const rows = stageRows(run({ total_seconds: 80, stages: [stage('Preparing', 8), stage('Translating', 72, 0.03)] }))
    expect(rows.map((r) => [r.name, r.duration, r.cost, r.share])).toEqual([
      ['Preparing', '8.0 s', null, 10],
      ['Translating', '1 min 12 s', '$0.03', 90],
    ])
  })

  it('falls back to the stages sum when the total is zero, and never exceeds 100%', () => {
    expect(stageRows(run({ total_seconds: 0, stages: [stage('A', 1), stage('B', 3)] })).map((r) => r.share)).toEqual([25, 75])
    // A total shorter than the stages (clock skew) uses the sum instead.
    expect(stageRows(run({ total_seconds: 2, stages: [stage('A', 4)] }))[0].share).toBe(100)
  })

  it('an all-zero run has zero-width bars; keys stay unique for repeated names', () => {
    const rows = stageRows(run({ total_seconds: 0, stages: [stage('Whole job', 0), stage('Whole job', 0)] }))
    expect(rows.map((r) => r.share)).toEqual([0, 0])
    expect(new Set(rows.map((r) => r.key)).size).toBe(2)
  })
})

describe('stageTotalLine / runsNote', () => {
  it('names the total and estimated spend', () => {
    expect(stageTotalLine(run({ total_seconds: 185, cost_usd: 0.05 }))).toBe('Total 3 min 5 s · estimated $0.05')
    expect(stageTotalLine(run({ total_seconds: 3.4 }))).toBe('Total 3.4 s')
    expect(stageTotalLine(run({ total_seconds: 30, running: true }))).toBe('Total 30 s so far')
  })

  it('mentions earlier runs only when there were some', () => {
    expect(runsNote(1)).toBeNull()
    expect(runsNote(3)).toBe('Latest of 3 runs.')
  })
})

describe('stageClock', () => {
  it('shows a stage start as a clock time and ignores a bad value', () => {
    expect(stageClock(1_700_000_000)).toMatch(/^\d{2}:\d{2}:\d{2}$/)
    expect(stageClock(Number.NaN)).toBe('')
    expect(stageRows(run({ stages: [stage('Translate', 5)] }))[0].at).toMatch(/\d{2}:\d{2}:\d{2}/)
  })
})

describe('recordedStageRows', () => {
  it('lists only the stages the run recorded, in run order, with their seconds', () => {
    const rows = recordedStageRows({ stage_seconds: { transcribe: 80, separate: 20, align: 'x', C: 5 } })
    expect(rows.map((r) => [r.name, r.duration])).toEqual([['Vocal separation', '20 s'], ['Speech recognition', '1 min 20 s']])
    expect(rows.map((r) => r.share)).toEqual([20, 80])
  })

  it('invents nothing when there are none', () => {
    for (const result of [null, {}, { stage_seconds: {} }, { stage_seconds: [1] }, { stage_seconds: { diarize: 9 } }]) {
      expect(recordedStageRows(result)).toEqual([])
    }
  })
})
