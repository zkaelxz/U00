import { describe, expect, it } from 'vitest'

import type { BenchmarkSet, Sweep, SweepEstimate, SweepRecommendation } from '../../api/benchmark'
import { canDecide, cerPoints, formatSweepTime, passesLine, startLabel, startProblem, stepLabel, sweepSets } from './sweepForm'

const set = (over: Partial<BenchmarkSet>): BenchmarkSet => ({ stage: 'transcription', tier: 'application', set_name: 's', case_count: 3, with_reference: 3, excluded: 0, ...over })

const est = (over: Partial<SweepEstimate> = {}): SweepEstimate => ({
  set_name: 's', language: 'zh', use_gpu: true, tuning_cases: 7, holdout_cases: 3, tuning_titles: 3, holdout_titles: 1,
  audio_seconds: 120, durations_guessed: false, small_set: true, min_set_cases: 50, models: [], knobs: [], not_swept: [],
  passes_done: 0, passes_to_run: 18, estimated_seconds: 600, estimated_cost_usd: 0, monthly_cap_usd: 0, monthly_refusal: null, ...over,
})

const rec = (state: SweepRecommendation['state']): SweepRecommendation => ({
  state, text: '', noise: 0, margin: 0.02, margin_rule: '',
  default: { engine: 'whisper', model: 'large-v3', changes: {}, label: 'large-v3' },
  winner: { engine: 'whisper', model: 'large-v3-turbo', changes: {}, label: 'large-v3-turbo' },
  tuning: { default_score: 0.8, winner_score: 0.9 }, holdout: null, tags: [], steps: [],
  case_counts: { tuning: 7, holdout: 3 }, small_set: true,
})

const sweep = (over: Partial<Sweep> = {}): Sweep => ({
  id: 'x', set_name: 's', language: 'zh', use_gpu: true, status: 'done', started_at: null, finished_at: null, note: null,
  passes_run: 16, passes_reused: 2, recommendation: rec('adopt'), decision: null, estimate: null, ...over,
})

describe('sweepSets', () => {
  it('lists only transcription sets with a scorable clip, once each', () => {
    const sets = [set({ set_name: 'b' }), set({ set_name: 'a' }), set({ set_name: 'a', tier: 'public' }),
      set({ set_name: 't', stage: 'translation' }), set({ set_name: 'gone', case_count: 2, excluded: 2 })]
    expect(sweepSets(sets)).toEqual(['a', 'b'])
  })
})

describe('start', () => {
  const base = { setName: 's', estimate: est(), estimateFresh: true, remote: false, running: false }
  it('needs a fresh estimate, the PC and no run going', () => {
    expect(startProblem(base)).toBeNull()
    expect(startProblem({ ...base, estimateFresh: false })).toBe('Estimate this sweep first.')
    expect(startProblem({ ...base, remote: true })).toMatch(/main PC/)
    expect(startProblem({ ...base, running: true })).toMatch(/run is going/)
    expect(startProblem({ ...base, estimate: est({ passes_to_run: 0 }) })).toMatch(/already run/)
  })
  it('says Resume once finished passes will be skipped', () => {
    expect(startLabel(est())).toBe('Start sweep')
    expect(startLabel(est({ passes_done: 4 }))).toBe('Resume sweep')
  })
})

describe('reading a sweep', () => {
  it('formats time and CER points', () => {
    expect(formatSweepTime(30)).toBe('under a minute')
    expect(formatSweepTime(720)).toBe('about 12 min')
    expect(formatSweepTime(12000)).toBe('about 3 h 20 min')
    expect(cerPoints(0.021)).toBe('2.1 CER points better')
    expect(cerPoints(-0.005)).toBe('0.5 CER points worse')
    expect(cerPoints(0)).toBe('no change')
  })
  it('labels a pass', () => {
    expect(stepLabel({ step: 'beam_size', value: 3, half: 'tuning', setup: '', run_id: 1, score: 1, errors: 0, kept: true })).toBe('Beam size 3')
    expect(stepLabel({ step: 'difficult_audio', value: true, half: 'tuning', setup: '', run_id: 1, score: 1, errors: 0, kept: false })).toBe('Difficult audio on')
    expect(stepLabel({ step: 'holdout', value: 'winner', half: 'holdout', setup: '', run_id: 1, score: 1, errors: 0, kept: null })).toBe('Hold-out: winner')
    expect(passesLine(sweep())).toBe('16 passes run, 2 reused from earlier runs')
  })
  it('allows approve only for a winner that held up, and one decision', () => {
    expect(canDecide(sweep())).toEqual({ approve: true, reject: true })
    expect(canDecide(sweep({ recommendation: rec('not_confirmed') }))).toEqual({ approve: false, reject: true })
    expect(canDecide(sweep({ recommendation: rec('keep_default') }))).toEqual({ approve: false, reject: false })
    expect(canDecide(sweep({ status: 'cancelled' }))).toEqual({ approve: false, reject: false })
    expect(canDecide(sweep({ decision: { decision: 'rejected', reason: '', decided_at: null, profile: null } }))).toEqual({ approve: false, reject: false })
  })
})
