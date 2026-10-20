import { describe, expect, it } from 'vitest'

import type { BenchmarkSet, SweepStep, TranslateSweepEstimate } from '../../types/benchmark'
import {
  chrfPoints, costLine, translateStartLabel, translateStartProblem, translateStepLabel, translateSweepKey, translateSweepSets,
} from './translateSweepForm'

const est = {
  estimated_cost_usd: 1.5, cap_applies: true, remaining_usd: 10, monthly_refusal: null, estimate_above_cap: false,
  passes_done: 0, passes_to_run: 7,
} as TranslateSweepEstimate
const ready = { setName: 's', engine: 'claude', estimate: est, estimateFresh: true, running: false }

describe('translation sweep form', () => {
  it('lists translation sets that have references, once, sorted', () => {
    const set = (stage: string | null, set_name: string, with_reference: number, excluded = 0) =>
      ({ stage, tier: 'application', set_name, case_count: 5, with_reference, excluded }) as BenchmarkSet
    expect(translateSweepSets([set('translation', 'b', 3), set('translation', 'a', 2), set('translation', 'a', 2),
      set('transcription', 'c', 9), set('translation', 'd', 0), set('translation', 'e', 2, 2)])).toEqual(['a', 'b'])
  })

  it('keys an estimate by everything it depends on', () => {
    expect(translateSweepKey('s', 'zh', 'claude', 'haiku', null)).not.toBe(translateSweepKey('s', 'zh', 'claude', 'haiku', 3))
  })

  it('words a score difference as chrF points', () => {
    expect(chrfPoints(0.02)).toBe('2.0 chrF points better')
    expect(chrfPoints(-0.005)).toBe('0.5 chrF points worse')
    expect(chrfPoints(0)).toBe('no change')
    expect(chrfPoints(null)).toBe('—')
  })

  it('offers Resume once a pass has run', () => {
    expect(translateStartLabel(est)).toBe('Start sweep')
    expect(translateStartLabel({ ...est, passes_done: 2 })).toBe('Resume sweep')
  })

  it('says why Start is off, in order', () => {
    expect(translateStartProblem(ready)).toBeNull()
    expect(translateStartProblem({ ...ready, running: true })).toMatch(/benchmark run/)
    expect(translateStartProblem({ ...ready, setName: '' })).toMatch(/golden set/)
    expect(translateStartProblem({ ...ready, estimateFresh: false })).toMatch(/Estimate/)
    expect(translateStartProblem({ ...ready, estimate: { ...est, monthly_refusal: 'Cap reached.' } })).toBe('Cap reached.')
    expect(translateStartProblem({ ...ready, estimate: { ...est, estimate_above_cap: true } })).toMatch(/more than/)
    expect(translateStartProblem({ ...ready, estimate: { ...est, passes_to_run: 0 } })).toMatch(/already run/)
  })

  it('describes the cost, free or capped', () => {
    expect(costLine({ ...est, cap_applies: false })).toMatch(/free/)
    expect(costLine(est)).toMatch(/left this month/)
  })

  it('names steps', () => {
    const step = (s: string, value: SweepStep['value']) => ({ step: s, value } as SweepStep)
    expect(translateStepLabel(step('noise', null))).toBe('Default (noise)')
    expect(translateStepLabel(step('batch_size', 15))).toBe('Batch size 15')
    expect(translateStepLabel(step('reflect', true))).toBe('Reflect on')
    expect(translateStepLabel(step('style_preset', 'audio_drama'))).toBe('Style preset audio drama')
    expect(translateStepLabel(step('holdout', 'winner'))).toBe('Hold-out: winner')
  })
})
