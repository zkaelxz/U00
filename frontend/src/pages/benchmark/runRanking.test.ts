import { describe, expect, it } from 'vitest'

import type { BenchmarkRun } from '../../types/benchmark'
import { bestValueIndex, bestValueRunIds, competitionRanks, runQualityRanks, sortedOrder, sortRuns } from './runRanking'

const run = (id: number, over: Partial<BenchmarkRun>): BenchmarkRun => ({
  id, label: '', stage: 'translation', engine: 'deepseek', model: null, prompt_version: '', arena_group: null,
  status: 'done', case_count: 2, scored_count: 2, passed_count: 1, error_count: 0, aggregate_score: 0.5,
  avg_latency_seconds: 1, total_cost_usd: 0.01, peak_vram_mb: null, note: null, created_at: null, finished_at: null,
  context_settings: {}, case_filter: {}, delta_vs_first: null, judge: null, picks: null, script_counts: null, stored_aggregate_score: null,
  timing: { cases: 0, start_median_ms: null, end_median_ms: null, within: null, merged: null, split: null, missed: null, silence: null }, timing_verdict: null, ...over,
})

describe('ranking helpers', () => {
  it('shares a rank between equal figures and skips missing ones', () => {
    expect(competitionRanks([3, 1, 3, null], true)).toEqual([1, 3, 1, null])
    expect(competitionRanks([3, 1, 3, null], false)).toEqual([2, 1, 2, null])
  })

  it('sorts best first, missing last, ties in the original order', () => {
    expect(sortedOrder([2, null, 1, 2])).toEqual([2, 0, 3, 1])
  })

  it('best value ignores free and cost-less rows and rows outside the top group', () => {
    const rows = [
      { quality: 0.9, cost: 0.1 },
      { quality: 0.89, cost: 0.02 },
      { quality: 0.5, cost: 0.001 },
      { quality: 0.95, cost: 0 },
      { quality: null, cost: 0.01 },
    ]
    expect(bestValueIndex(rows, 0.02)).toBe(1)
    expect(bestValueIndex([{ quality: 1, cost: null }], 0.02)).toBe(-1)
  })
})

describe('recorded runs', () => {
  const runs = [
    run(1, { aggregate_score: 0.6, avg_latency_seconds: 5, total_cost_usd: 0.5 }),
    run(2, { aggregate_score: 0.8, avg_latency_seconds: 9, total_cost_usd: 0.4 }),
    run(3, { aggregate_score: null, scored_count: 0, avg_latency_seconds: 1, total_cost_usd: null }),
    run(4, { stage: 'transcription', aggregate_score: 0.3 }),
    run(5, { status: 'failed', aggregate_score: 0.99 }),
  ]

  it('ranks within a stage, and leaves unscored, failed and other-stage runs out of it', () => {
    const ranks = runQualityRanks(runs)
    expect([...ranks]).toEqual([[1, 2], [2, 1], [4, 1]])
  })

  it('orders by quality, speed or cost, best first, with unmeasured runs last', () => {
    const ranks = runQualityRanks(runs)
    expect(sortRuns(runs, 'quality', ranks).map((r) => r.id)).toEqual([2, 4, 1, 3, 5])
    expect(sortRuns(runs, 'speed', ranks).map((r) => r.id)).toEqual([3, 4, 5, 1, 2])
    expect(sortRuns(runs, 'cost', ranks).map((r) => r.id)).toEqual([4, 5, 2, 1, 3])
  })

  it('marks one best-value run per stage', () => {
    expect([...bestValueRunIds(runs)].sort()).toEqual([2, 4])
  })
})
