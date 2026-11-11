import { describe, expect, it } from 'vitest'

import type { BenchmarkRun } from '../../types/benchmark'
import { summariseModels } from './modelSummary'

const run = (id: number, over: Partial<BenchmarkRun>): BenchmarkRun => ({
  id, label: '', stage: 'translation', engine: 'deepseek', model: null, prompt_version: '', arena_group: null,
  status: 'done', case_count: 2, scored_count: 2, passed_count: 1, error_count: 0, aggregate_score: 0.5,
  avg_latency_seconds: 1, total_cost_usd: 0.01, peak_vram_mb: null, note: null, created_at: null, finished_at: null,
  context_settings: {}, case_filter: {}, delta_vs_first: null, judge: null, picks: null, script_counts: null, stored_aggregate_score: null,
  timing: { cases: 0, start_median_ms: null, end_median_ms: null, within: null, merged: null, split: null, missed: null, silence: null }, timing_verdict: null, ...over,
})

describe('summariseModels', () => {
  it('averages a model over its finished runs and ranks within a stage', () => {
    const rows = summariseModels([
      run(1, { engine: 'deepseek', aggregate_score: 0.6, avg_latency_seconds: 2 }),
      run(2, { engine: 'deepseek', aggregate_score: 0.8, avg_latency_seconds: 4 }),
      run(3, { engine: 'claude', aggregate_score: 0.9, avg_latency_seconds: 6 }),
      run(4, { engine: 'whisper', model: 'large-v3', stage: 'transcription', aggregate_score: 0.5 }),
    ])
    const ds = rows.find((r) => r.key.includes('deepseek'))!
    expect(ds.runs).toBe(2)
    expect(ds.score).toBeCloseTo(0.7)
    expect(ds.latency).toBeCloseTo(3)
    expect(ds.qualityRank).toBe(2)
    expect(ds.speedRank).toBe(1)
    expect(rows.find((r) => r.key.includes('claude'))!.qualityRank).toBe(1)
    expect(rows.find((r) => r.key.includes('whisper'))!.qualityRank).toBe(1)
  })

  it('skips unfinished runs and leaves unmeasured figures blank and unranked', () => {
    const rows = summariseModels([
      run(1, { engine: 'claude', status: 'failed', aggregate_score: 0.99 }),
      run(2, { engine: 'ollama', scored_count: 0, aggregate_score: null, avg_latency_seconds: null }),
    ])
    expect(rows).toHaveLength(1)
    expect(rows[0].score).toBeNull()
    expect(rows[0].qualityRank).toBeNull()
    expect(rows[0].speedRank).toBeNull()
  })
})
